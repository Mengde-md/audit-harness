"""Access-code gate with signed, expiring HttpOnly sessions."""
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict

from starlette.requests import Request
from starlette.responses import JSONResponse


class SiteAccessMiddleware:
    cookie_name = "audit_site_access"
    lifetime = 8 * 60 * 60

    def __init__(self, app, settings):
        self.app = app
        self.code = settings.site_access_code
        self.secret = (settings.site_session_secret or secrets.token_urlsafe(48)).encode()
        self.attempts = OrderedDict()

    def granted(self, request):
        if not self.code:
            return True
        value = request.cookies.get(self.cookie_name, "")
        try:
            expires, nonce, signature = value.split(".")
            message = f"{expires}.{nonce}".encode()
            expected = hmac.new(self.secret, message, hashlib.sha256).hexdigest()
            return int(expires) > time.time() and hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        path = scope.get("path", "")
        response = None
        if path == "/api/site-access":
            response = await self.access(request)
        elif path == "/api/health":
            response = JSONResponse({"status": "UP"})
        elif (path.startswith("/api/") or path in {"/docs", "/redoc", "/openapi.json"}) and not self.granted(request):
            response = JSONResponse(
                {"code": "SITE_ACCESS_REQUIRED", "msg": "请输入访问码", "data": None},
                status_code=401,
            )
        if response is not None:
            response.headers["Cache-Control"] = "no-store"
            await response(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    async def access(self, request):
        if request.method == "GET":
            return JSONResponse({"required": bool(self.code), "granted": self.granted(request)})
        if request.method == "DELETE":
            response = JSONResponse({"granted": False})
            response.delete_cookie(self.cookie_name, path="/", httponly=True, samesite="lax", secure=request.url.scheme == "https")
            return response
        if request.method != "POST":
            return JSONResponse({"msg": "请求方法不支持"}, status_code=405)
        # This endpoint never enters request-body logging. Keep guesses bounded.
        now = time.monotonic()
        client = request.client.host if request.client else "unknown"
        self.attempts = OrderedDict((key, value) for key, value in self.attempts.items() if value[0] > now - 60)
        window, count = self.attempts.get(client, (now, 0))
        if count >= 10:
            return JSONResponse({"msg": "尝试过于频繁，请稍后再试"}, status_code=429, headers={"Retry-After": "60"})
        self.attempts[client] = (window, count + 1)
        if len(self.attempts) > 2048:
            self.attempts.popitem(last=False)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024:
                return JSONResponse({"msg": "请求内容过大"}, status_code=413)
        try:
            payload = json.loads(body)
            code = payload.get("code") if isinstance(payload, dict) else None
        except (ValueError, UnicodeDecodeError):
            code = None
        if not isinstance(code, str) or not hmac.compare_digest(code.encode(), self.code.encode()):
            return JSONResponse({"msg": "访问码错误"}, status_code=401)
        self.attempts.pop(client, None)
        expires = int(time.time()) + self.lifetime
        message = f"{expires}.{secrets.token_urlsafe(24)}"
        signature = hmac.new(self.secret, message.encode(), hashlib.sha256).hexdigest()
        response = JSONResponse({"granted": True})
        response.set_cookie(self.cookie_name, f"{message}.{signature}", max_age=self.lifetime,
                            httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
        return response
