import json

from app.cli.client import ApiError
from app.cli.main import main


class FakeClient:
    def __init__(self) -> None:
        self.created_payloads = []
        self.status_responses = []
        self.report_response = {}

    def create_audit(self, payload):
        self.created_payloads.append(payload)
        return {"audit_id": "audit-1", "status": "running"}

    def get_status(self, audit_id):
        assert audit_id == "audit-1"
        if self.status_responses:
            return self.status_responses.pop(0)
        return {
            "audit_id": audit_id,
            "audit_mode": "full",
            "status": "passed",
            "profile": "python-basic",
            "summary": {
                "passed_steps": 1,
                "failed_steps": 0,
                "quality": {
                    "gate": {"status": "pass"},
                    "confidence": {"level": "high", "score": 1.0},
                },
                "artifacts": {"report_markdown": "tmp/audits/audit-1/report.md"},
            },
            "steps": [{"name": "api_acceptance", "status": "passed"}],
        }

    def get_report(self, audit_id):
        assert audit_id == "audit-1"
        return self.report_response


def test_cli_audit_builds_full_request(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    api_doc = tmp_path / "api.md"
    api_doc.write_text("# API", encoding="utf-8")
    client = FakeClient()

    code = main(
        [
            "audit",
            "--repo",
            str(repo),
            "--api-doc-file",
            str(api_doc),
            "--mode",
            "full",
        ],
        client=client,
    )

    assert code == 0
    assert client.created_payloads[0]["repo"] == str(repo)
    assert client.created_payloads[0]["audit_mode"] == "full"
    assert client.created_payloads[0]["api_document_markdown"] == "# API"
    assert client.created_payloads[0]["llm_mock_profile"] == "generic-agent"
    assert "audit_id: audit-1" in capsys.readouterr().out


def test_cli_audit_reads_patch_file_for_change_mode(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    api_doc = tmp_path / "api.md"
    api_doc.write_text("# API", encoding="utf-8")
    patch = tmp_path / "input.patch"
    patch.write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
    client = FakeClient()

    code = main(
        [
            "audit",
            "--repo",
            str(repo),
            "--api-doc-file",
            str(api_doc),
            "--mode",
            "change",
            "--base-commit",
            "main",
            "--patch-file",
            str(patch),
        ],
        client=client,
    )

    assert code == 0
    assert client.created_payloads[0]["base_commit"] == "main"
    assert client.created_payloads[0]["patch"] == "diff --git a/app.py b/app.py\n"


def test_cli_status_formats_task_status(capsys):
    client = FakeClient()

    code = main(["status", "--audit-id", "audit-1"], client=client)

    assert code == 0
    output = capsys.readouterr().out
    assert "Audit Status" in output
    assert "gate: pass" in output


def test_cli_watch_stops_when_task_completes(capsys):
    client = FakeClient()
    client.status_responses = [
        {
            "audit_id": "audit-1",
            "audit_mode": "full",
            "status": "running",
            "profile": "python-basic",
            "summary": {},
            "steps": [{"name": "python_compile", "status": "passed"}],
        },
        {
            "audit_id": "audit-1",
            "audit_mode": "full",
            "status": "passed",
            "profile": "python-basic",
            "summary": {
                "quality": {
                    "gate": {"status": "pass"},
                    "confidence": {"level": "high", "score": 1.0},
                },
                "artifacts": {"report_markdown": "tmp/report.md"},
            },
            "steps": [{"name": "api_acceptance", "status": "passed"}],
        },
    ]

    code = main(
        ["watch", "--audit-id", "audit-1", "--interval", "1", "--timeout", "5"],
        client=client,
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "running" in output
    assert "Audit completed" in output


def test_cli_returns_nonzero_for_api_error(capsys):
    class ErrorClient(FakeClient):
        def get_status(self, audit_id):
            raise ApiError(20001, "audit not found")

    code = main(["status", "--audit-id", "audit-1"], client=ErrorClient())

    assert code == 1
    assert "code: 20001" in capsys.readouterr().err


def test_cli_json_outputs_raw_response(capsys):
    client = FakeClient()

    code = main(["status", "--audit-id", "audit-1", "--json"], client=client)

    assert code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["audit_id"] == "audit-1"


def test_cli_server_env_var_is_used(monkeypatch):
    monkeypatch.setenv("AUDIT_HARNESS_SERVER", "http://example.test")

    from app.cli.main import build_parser, resolve_server

    args = build_parser().parse_args(["status", "--audit-id", "audit-1"])

    assert resolve_server(args) == "http://example.test"
