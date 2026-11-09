"""Check actual sandbox prerequisites without changing the host."""
import asyncio
import time

import docker

_cached = None
_cached_at = 0.0


def _probe(settings):
    client = None
    try:
        client = docker.from_env(timeout=2)
        client.ping()
        if settings.app_env.lower() in {"production", "prod"}:
            info = client.info()
            if (
                "name=rootless" not in info.get("SecurityOptions", [])
                or info.get("CgroupVersion") != "2"
                or info.get("CgroupDriver") != "systemd"
                or not all(info.get(key) for key in ("MemoryLimit", "CpuCfsQuota", "PidsLimit"))
            ):
                return {"sandbox_available": False, "sandbox_reason": "Docker 沙箱需要启用 rootless 和资源限制"}
        for image in set(settings.python_runtime_images.values()) | {settings.python_sandbox_image}:
            client.images.get(image)
        return {"sandbox_available": True, "sandbox_reason": "Docker 沙箱与 Python 审计镜像已就绪"}
    except docker.errors.ImageNotFound:
        return {"sandbox_available": False, "sandbox_reason": "服务器缺少 Python 审计沙箱镜像，需要先完成沙箱部署"}
    except Exception:
        return {"sandbox_available": False, "sandbox_reason": "服务器尚未配置可用的 Docker 沙箱，当前无法执行审计"}
    finally:
        if client is not None:
            client.close()


async def sandbox_readiness(settings):
    global _cached, _cached_at
    if _cached is None or time.monotonic() - _cached_at > 10:
        _cached = await asyncio.to_thread(_probe, settings)
        _cached_at = time.monotonic()
    return dict(_cached)
