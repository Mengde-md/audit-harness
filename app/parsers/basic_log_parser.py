import re

from app.parsers.evidence_parser import EvidenceParser


class BasicLogParser(EvidenceParser):
    _file_line = re.compile(r'File "([^"]+)", line (\d+)')
    _pytest_failure = re.compile(r"^FAILED\s+([^:\s]+)(?:::(\S+))?", re.MULTILINE)

    def parse(self, step_name: str, stdout: str, stderr: str) -> list[dict]:
        combined = f"{stdout}\n{stderr}"
        findings: list[dict] = []

        for match in self._pytest_failure.finditer(combined):
            findings.append(
                {
                    "severity": "high",
                    "type": "test_failure",
                    "file": match.group(1),
                    "line": None,
                    "message": match.group(0),
                }
            )

        for match in self._file_line.finditer(combined):
            findings.append(
                {
                    "severity": "high",
                    "type": "traceback",
                    "file": match.group(1),
                    "line": int(match.group(2)),
                    "message": f"Python traceback during {step_name}",
                }
            )

        if not findings and stderr.strip():
            findings.append(
                {
                    "severity": "medium",
                    "type": "stderr",
                    "file": None,
                    "line": None,
                    "message": stderr.strip()[-1000:],
                }
            )
        return findings
