"""AST-based deterministic Python code review scanner."""

import ast
from pathlib import Path

from app.code_review.models import ReviewFinding


EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "migrations",
    "node_modules",
    "tmp",
    "tests",
}

DEFAULT_SCAN_DIRS = ("app", "backend", "src", "api")
SECRET_NAME_HINTS = ("password", "passwd", "secret", "token", "api_key", "access_key")
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "request"}
SQL_KEYWORDS = ("select", "insert", "update", "delete", "where", "from")


class CodeReviewScanner:
    """Run deterministic code review rules without executing project code."""

    rule_count = 8

    def collect_files(self, root: Path, scoped_files: list[Path] | None = None) -> list[Path]:
        """Collect Python files from an explicit patch scope or common source dirs."""
        if scoped_files is not None:
            return [
                path
                for path in (root / item for item in scoped_files)
                if path.exists() and path.is_file() and self._is_allowed(path, root)
            ]

        candidates: list[Path] = []
        scan_roots = [root / name for name in DEFAULT_SCAN_DIRS if (root / name).exists()]
        if not scan_roots:
            scan_roots = [root]
        for scan_root in scan_roots:
            candidates.extend(
                path
                for path in scan_root.rglob("*.py")
                if path.is_file() and self._is_allowed(path, root)
            )
        return sorted(candidates)

    def scan(self, root: Path, files: list[Path]) -> list[ReviewFinding]:
        """Scan files and return normalized findings."""
        findings: list[ReviewFinding] = []
        for path in files:
            relative = self._relative(path, root)
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                findings.append(
                    ReviewFinding(
                        source="deterministic",
                        rule_id="PY-SYNTAX-001",
                        severity="high",
                        category="syntax",
                        file=relative,
                        line=getattr(exc, "lineno", 1) or 1,
                        message="Python file cannot be parsed by AST scanner.",
                        suggestion="Fix syntax or encoding before relying on automated review.",
                        evidence=str(exc),
                    )
                )
                continue
            visitor = _PythonRuleVisitor(relative, source)
            visitor.visit(tree)
            findings.extend(visitor.findings)
        return findings

    @staticmethod
    def _is_allowed(path: Path, root: Path) -> bool:
        """Exclude generated, virtualenv, test, and cache directories."""
        try:
            relative = path.relative_to(root)
        except ValueError:
            return False
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            return False
        if path.stat().st_size > 2 * 1024 * 1024:
            return False
        return not any(part in EXCLUDED_DIRS for part in relative.parts)

    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        """Format file paths relative to the audited project root."""
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)


class _PythonRuleVisitor(ast.NodeVisitor):
    """AST visitor implementing first-layer deterministic review rules."""

    def __init__(self, file: str, source: str):
        self.file = file
        self.source = source
        self.findings: list[ReviewFinding] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        """Detect simple hardcoded secrets."""
        target_names = [self._target_name(target) for target in node.targets]
        self._check_hardcoded_secret(node, target_names, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Detect annotated hardcoded secrets."""
        self._check_hardcoded_secret(
            node,
            [self._target_name(node.target)],
            node.value,
        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Detect unsafe calls, missing timeouts, and fragile SQL execution."""
        call_name = self._call_name(node.func)
        if call_name in {"eval", "exec"}:
            self._add(
                "PY-SEC-002",
                "high",
                "security",
                node,
                f"Use of `{call_name}` can execute untrusted code.",
                "Replace dynamic execution with explicit parsing or a safe dispatch table.",
            )
        if call_name in {"subprocess.run", "subprocess.call", "subprocess.Popen"}:
            if self._keyword_is_true(node, "shell"):
                self._add(
                    "PY-SEC-004",
                    "high",
                    "security",
                    node,
                    "subprocess is invoked with shell=True.",
                    "Pass arguments as a list and keep shell=False unless strictly required.",
                )
        if self._is_http_call_without_timeout(call_name, node):
            self._add(
                "PY-REL-001",
                "medium",
                "reliability",
                node,
                "HTTP call does not set an explicit timeout.",
                "Add a bounded timeout so external dependencies cannot hang the service.",
            )
        if call_name.endswith(".execute") and node.args:
            first_arg = node.args[0]
            if self._looks_like_dynamic_sql(first_arg):
                self._add(
                    "PY-SEC-005",
                    "high",
                    "security",
                    node,
                    "SQL execution appears to use string interpolation or concatenation.",
                    "Use parameterized queries or ORM bind parameters.",
                    evidence=ast.get_source_segment(self.source, first_arg),
                )
        if call_name.startswith(("requests.", "httpx.")):
            if self._keyword_is_false(node, "verify"):
                self._add(
                    "PY-SEC-006",
                    "medium",
                    "security",
                    node,
                    "TLS certificate verification is disabled.",
                    "Keep certificate verification enabled in production paths.",
                )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Detect swallowed broad exceptions."""
        if self._is_broad_exception(node.type) and self._body_is_only_pass(node.body):
            self._add(
                "PY-REL-002",
                "medium",
                "reliability",
                node,
                "Broad exception is swallowed with pass.",
                "Log the exception or handle a specific error type with a clear fallback.",
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detect overly long functions."""
        self._check_function_length(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Detect overly long async functions."""
        self._check_function_length(node)
        self.generic_visit(node)

    def _check_hardcoded_secret(
        self,
        node: ast.AST,
        target_names: list[str | None],
        value: ast.AST | None,
    ) -> None:
        """Flag suspicious secret-like variable names assigned literal strings."""
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return
        if not value.value.strip() or len(value.value.strip()) < 8:
            return
        for name in target_names:
            if not name:
                continue
            lower_name = name.lower()
            if any(hint in lower_name for hint in SECRET_NAME_HINTS):
                self._add(
                    "PY-SEC-001",
                    "medium",
                    "security",
                    node,
                    f"`{name}` appears to contain a hardcoded secret.",
                    "Read secrets from environment variables or a secret manager.",
                )
                return

    def _check_function_length(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Flag functions long enough to hide multiple responsibilities."""
        if node.end_lineno is None:
            return
        line_count = node.end_lineno - node.lineno + 1
        if line_count > 80:
            self._add(
                "PY-MAINT-001",
                "low",
                "maintainability",
                node,
                f"Function `{node.name}` is {line_count} lines long.",
                "Split validation, I/O, and business logic into smaller named functions.",
            )

    def _is_http_call_without_timeout(self, call_name: str, node: ast.Call) -> bool:
        """Detect common requests/httpx calls that omit timeout."""
        if not any(call_name.startswith(prefix) for prefix in ("requests.", "httpx.")):
            return False
        method = call_name.rsplit(".", 1)[-1]
        if method not in HTTP_METHODS:
            return False
        return not any(keyword.arg == "timeout" for keyword in node.keywords)

    def _looks_like_dynamic_sql(self, node: ast.AST) -> bool:
        """Return true for f-strings and string concatenation that look like SQL."""
        if isinstance(node, ast.JoinedStr):
            return self._contains_sql_keyword(ast.get_source_segment(self.source, node))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._contains_sql_keyword(ast.get_source_segment(self.source, node))
        return False

    @staticmethod
    def _contains_sql_keyword(value: str | None) -> bool:
        """Check whether a source segment resembles SQL."""
        lower = (value or "").lower()
        return any(keyword in lower for keyword in SQL_KEYWORDS)

    @staticmethod
    def _body_is_only_pass(body: list[ast.stmt]) -> bool:
        """Return true for empty-looking exception handlers."""
        return len(body) == 1 and isinstance(body[0], ast.Pass)

    @staticmethod
    def _is_broad_exception(node: ast.expr | None) -> bool:
        """Detect bare except and except Exception/BaseException."""
        if node is None:
            return True
        if isinstance(node, ast.Name):
            return node.id in {"Exception", "BaseException"}
        return False

    @staticmethod
    def _keyword_is_true(node: ast.Call, name: str) -> bool:
        """Return true when a call keyword is literal True."""
        return any(
            keyword.arg == name
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        )

    @staticmethod
    def _keyword_is_false(node: ast.Call, name: str) -> bool:
        """Return true when a call keyword is literal False."""
        return any(
            keyword.arg == name
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in node.keywords
        )

    @classmethod
    def _call_name(cls, node: ast.AST) -> str:
        """Return a dotted name for simple calls."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = cls._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        if isinstance(node, ast.Call):
            return cls._call_name(node.func)
        return ""

    @staticmethod
    def _target_name(node: ast.AST) -> str | None:
        """Extract simple assignment target names."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _add(
        self,
        rule_id: str,
        severity: str,
        category: str,
        node: ast.AST,
        message: str,
        suggestion: str,
        evidence: str | None = None,
    ) -> None:
        """Append one normalized finding."""
        self.findings.append(
            ReviewFinding(
                source="deterministic",
                rule_id=rule_id,
                severity=severity,
                category=category,
                file=self.file,
                line=getattr(node, "lineno", 1) or 1,
                message=message,
                suggestion=suggestion,
                evidence=evidence,
            )
        )

