"""Audit Harness command-line entrypoint."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from app.cli.client import ApiError, CliError, HarnessHttpClient, HttpRequestError
from app.cli.formatters import (
    format_audit_submitted,
    format_json,
    format_report,
    format_status,
    format_watch_completed,
    format_watch_tick,
    is_terminal_status,
)

DEFAULT_SERVER = "http://127.0.0.1:8000"


class HarnessClient(Protocol):
    """Protocol used by tests to inject a fake client."""

    def create_audit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an audit task."""

    def get_status(self, audit_id: str) -> dict[str, Any]:
        """Fetch audit status."""

    def get_report(self, audit_id: str) -> dict[str, Any]:
        """Fetch audit report."""


class CliArgumentError(CliError):
    """Raised when CLI arguments are invalid."""

    exit_code = 2


class WatchTimeoutError(CliError):
    """Raised when watch times out before audit completion."""

    exit_code = 4


def main(
    argv: Sequence[str] | None = None,
    client: HarnessClient | None = None,
) -> int:
    """Run the Audit Harness CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        selected_client = client or HarnessHttpClient(resolve_server(args))
        output = dispatch(args, selected_client)
    except CliArgumentError as exc:
        print(f"Error\nmsg: {exc}", file=sys.stderr)
        return exc.exit_code
    except ApiError as exc:
        print(f"Error\ncode: {exc.code}\nmsg: {exc.msg}", file=sys.stderr)
        return exc.exit_code
    except HttpRequestError as exc:
        print(f"Error\nmsg: {exc}", file=sys.stderr)
        return exc.exit_code
    except CliError as exc:
        print(f"Error\nmsg: {exc}", file=sys.stderr)
        return exc.exit_code
    if output:
        print(output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(prog="audit-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_audit_parser(subparsers)
    _add_status_parser(subparsers)
    _add_watch_parser(subparsers)
    _add_report_parser(subparsers)
    return parser


def _add_audit_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the audit subcommand."""
    parser = subparsers.add_parser("audit", help="submit an audit task")
    _add_server_argument(parser)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--mode", choices=("full", "change"), default="full")
    parser.add_argument("--api-doc-file", required=True)
    parser.add_argument("--patch-file")
    parser.add_argument("--base-commit")
    parser.add_argument("--llm-policy", choices=("mock", "disabled"), default="mock")
    parser.add_argument("--requester-id", default="cli")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--json", action="store_true")


def _add_status_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the status subcommand."""
    parser = subparsers.add_parser("status", help="show audit task status")
    _add_server_argument(parser)
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--json", action="store_true")


def _add_watch_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the watch subcommand."""
    parser = subparsers.add_parser("watch", help="poll audit task until completion")
    _add_server_argument(parser)
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--interval", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--json", action="store_true")


def _add_report_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the report subcommand."""
    parser = subparsers.add_parser("report", help="show audit report summary")
    _add_server_argument(parser)
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--path-only", action="store_true")


def _add_server_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared server option to one subcommand."""
    parser.add_argument(
        "--server",
        default=None,
        help="Harness HTTP server, default uses AUDIT_HARNESS_SERVER or localhost",
    )


def resolve_server(args: argparse.Namespace) -> str:
    """Resolve server from CLI argument, environment, or default."""
    return args.server or os.getenv("AUDIT_HARNESS_SERVER") or DEFAULT_SERVER


def dispatch(args: argparse.Namespace, client: HarnessClient) -> str:
    """Dispatch one parsed command."""
    if args.command == "audit":
        return run_audit(args, client)
    if args.command == "status":
        data = client.get_status(args.audit_id)
        return format_json(data) if args.json else format_status(data)
    if args.command == "watch":
        data = run_watch(args, client)
        return format_json(data) if args.json else format_watch_completed(data)
    if args.command == "report":
        data = client.get_report(args.audit_id)
        return format_json(data) if args.json else format_report(data, args.path_only)
    raise CliArgumentError(f"unknown command: {args.command}")


def run_audit(args: argparse.Namespace, client: HarnessClient) -> str:
    """Submit an audit task and optionally wait for completion."""
    payload = build_audit_payload(args)
    data = client.create_audit(payload)
    if args.wait:
        audit_id = data.get("audit_id")
        if not audit_id:
            raise CliArgumentError("audit response missing audit_id")
        completed = wait_for_audit(
            client,
            str(audit_id),
            interval=3,
            timeout=args.timeout,
            emit_ticks=False,
        )
        return format_json(completed) if args.json else format_watch_completed(completed)
    return format_json(data) if args.json else format_audit_submitted(data)


def run_watch(args: argparse.Namespace, client: HarnessClient) -> dict[str, Any]:
    """Poll audit status until a terminal status is reached."""
    return wait_for_audit(
        client,
        args.audit_id,
        interval=args.interval,
        timeout=args.timeout,
        emit_ticks=not args.json,
    )


def wait_for_audit(
    client: HarnessClient,
    audit_id: str,
    interval: int,
    timeout: int,
    emit_ticks: bool,
) -> dict[str, Any]:
    """Poll the Harness service until the audit finishes or times out."""
    start = time.monotonic()
    last_line = ""
    while True:
        elapsed = int(time.monotonic() - start)
        if elapsed > timeout:
            raise WatchTimeoutError(f"watch timed out after {timeout}s")
        data = client.get_status(audit_id)
        status = str(data.get("status", ""))
        line = format_watch_tick(data, elapsed)
        if emit_ticks and line != last_line:
            print(line)
            last_line = line
        if is_terminal_status(status):
            return data
        time.sleep(max(1, interval))


def build_audit_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Build the POST /audits request body from CLI arguments."""
    repo = Path(args.repo).expanduser()
    if not repo.exists():
        raise CliArgumentError(f"repo path does not exist: {repo}")
    api_doc_file = Path(args.api_doc_file).expanduser()
    if not api_doc_file.exists():
        raise CliArgumentError(f"api doc file does not exist: {api_doc_file}")
    payload: dict[str, Any] = {
        "requester_id": args.requester_id,
        "repo": str(repo),
        "audit_mode": args.mode,
        "llm_policy": args.llm_policy,
        "api_document_markdown": api_doc_file.read_text(encoding="utf-8"),
    }
    if args.llm_policy == "mock":
        payload["llm_mock_profile"] = "generic-agent"
    if args.mode == "change":
        if not args.base_commit:
            raise CliArgumentError("change mode requires --base-commit")
        if not args.patch_file:
            raise CliArgumentError("change mode requires --patch-file")
        patch_file = Path(args.patch_file).expanduser()
        if not patch_file.exists():
            raise CliArgumentError(f"patch file does not exist: {patch_file}")
        payload["base_commit"] = args.base_commit
        payload["patch"] = patch_file.read_text(encoding="utf-8")
    elif args.patch_file or args.base_commit:
        raise CliArgumentError("full mode does not accept --patch-file or --base-commit")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
