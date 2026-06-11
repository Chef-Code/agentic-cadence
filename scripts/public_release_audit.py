"""Audit release-readiness invariants before making the repository public."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHA_PIN_RE = re.compile(r"@[0-9a-f]{40}$")
USES_RE = re.compile(r"^(?:-\s*)?uses:\s*(?P<value>\S+)\s*$")

DISALLOWED_TOKENS = (
    "Chef-Code/" + "codex-" + "transmission-control",
    "codex-" + "transmission-control",
    "W" + "DA",
    "wda" + "-systems",
    "wda" + "mo",
    "C:" + "\\Users\\",
)

REQUIRED_CODEOWNERS = (
    ".github/workflows/**",
    ".github/CODEOWNERS",
    "scripts/codex_review_preflight.py",
    "scripts/validate_protocol.py",
    "scripts/public_release_audit.py",
    "codex_cadence/cli.py",
    "codex_cadence/release.py",
    "tests/test_release_dry_run.py",
)


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)


def tracked_files() -> list[str]:
    result = run_git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    return [path for path in result.stdout.split("\0") if path]


def read_text(relative: str) -> str | None:
    path = ROOT / relative
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def scan_current_tree() -> list[str]:
    findings: list[str] = []
    for relative in tracked_files():
        text = read_text(relative)
        if text is None:
            continue
        for token in DISALLOWED_TOKENS:
            if token in text:
                findings.append(f"{relative}: contains private/public-release-blocking token {token!r}")
    return findings


def workflow_uses_lines() -> list[tuple[str, int, str]]:
    lines: list[tuple[str, int, str]] = []
    workflows = ROOT / ".github" / "workflows"
    for path in sorted(workflows.glob("*.yml")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = USES_RE.match(line.strip())
            if match is None:
                continue
            value = match.group("value").strip("'\"")
            lines.append((path.relative_to(ROOT).as_posix(), line_no, value))
    return lines


def check_workflow_pins() -> list[str]:
    findings: list[str] = []
    for relative, line_no, value in workflow_uses_lines():
        if value.startswith("./"):
            continue
        if not SHA_PIN_RE.search(value):
            findings.append(f"{relative}:{line_no}: action is not pinned to a full commit SHA: {value}")
    return findings


def check_codeowners() -> list[str]:
    path = ROOT / ".github" / "CODEOWNERS"
    if not path.exists():
        return [".github/CODEOWNERS: missing public-release owner review rules"]

    text = path.read_text(encoding="utf-8")
    findings = []
    for token in REQUIRED_CODEOWNERS:
        if token not in text:
            findings.append(f".github/CODEOWNERS: missing required owner rule for {token}")
    return findings


def scan_history() -> list[str]:
    result = run_git(["rev-list", "--all"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    findings: list[str] = []
    commits = [line for line in result.stdout.splitlines() if line]
    for token in DISALLOWED_TOKENS:
        for commit in commits:
            grep = run_git(["grep", "-I", "-n", "-F", token, commit, "--"])
            if grep.returncode == 0:
                for line in grep.stdout.splitlines():
                    findings.append(f"history:{line}")
            elif grep.returncode not in (1,):
                raise RuntimeError(grep.stderr or grep.stdout)
    return findings


def run_audit(include_history: bool) -> list[str]:
    findings = []
    findings.extend(scan_current_tree())
    findings.extend(check_workflow_pins())
    findings.extend(check_codeowners())
    if include_history:
        findings.extend(scan_history())
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Agentic Cadence public-release readiness.")
    parser.add_argument(
        "--history",
        action="store_true",
        help="Also scan all reachable Git history. This must pass only in the clean public mirror/rewrite.",
    )
    args = parser.parse_args(argv)

    findings = run_audit(include_history=args.history)
    if findings:
        for finding in findings:
            print(finding)
        return 1

    print("public release audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
