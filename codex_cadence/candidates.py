from __future__ import annotations

import io
import json
import re
import subprocess
import tokenize
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .epochs import elect_candidates
from .github_evidence import pr_check_failure_findings

DISCOVERY_INTENTS = ("merge_readiness", "repo_health", "product_evolution", "hybrid")
DISCOVERY_MODES = ("off", "local", "expanded")
PROPOSAL_ALLOWANCES = ("none", "surface", "elect")
DEPENDENCY_FAN_OUT_VALUES = ("low", "medium", "high")
RUN_SIGNAL_VALUES = ("low", "medium", "high")
BUSINESS_VALUE_VALUES = ("low", "medium", "high")
BUSINESS_MEMORY_CLASSIFICATIONS = (
    "direction",
    "business_rule",
    "problem",
    "feature",
    "nice_to_have",
    "risk",
    "constraint",
    "unknown",
)
BUSINESS_MEMORY_PATH = Path("docs/cadence/business-memory.md")
BUSINESS_MEMORY_SOURCE = "business_memory"
BUSINESS_MEMORY_STATUSES = ("active", "fulfilled", "superseded")
BUSINESS_MEMORY_TIME_SAVED_SCORE = {"low": 35, "medium": 55, "high": 75}
BUSINESS_MEMORY_RISK_BONUS = {"low": 0, "medium": 5, "high": 10}
BUSINESS_MEMORY_SAFETY_TERMS = (
    "security",
    "privacy",
    "billing",
    "invoice",
    "accounting",
    "audit",
    "compliance",
    "authorization",
    "permission",
    "data",
    "integrity",
    "customer",
    "user",
    "document",
    "workflow",
    "availability",
    "deployment",
    "reliability",
    "scheduling",
    "dispatch",
    "tariff",
    "closeout",
    "bol",
)
BUSINESS_MEMORY_REPO_HEALTH_WORKFLOW_TERMS = (
    "security",
    "privacy",
    "billing",
    "invoice",
    "accounting",
    "audit",
    "compliance",
    "authorization",
    "permission",
    "data integrity",
    "availability",
    "deployment",
    "reliability",
    "scheduling",
    "dispatch",
    "tariff",
    "closeout",
    "bol",
)
BUSINESS_MEMORY_CLASSIFICATION_TERMS = {
    "direction": ("direction", "strategy", "roadmap", "goal", "vision", "north star"),
    "business_rule": ("rule", "must", "must not", "policy", "permission", "authority", "invariant"),
    "problem": ("pain", "manual", "repeat", "confusing", "broken", "bug", "failure", "workaround"),
    "feature": ("feature", "capability", "request", "missing", "support", "allow"),
    "nice_to_have": ("nice to have", "polish", "convenience", "quality of life", "optimize"),
    "risk": ("risk", "security", "privacy", "compliance", "audit", "billing", "data integrity"),
    "constraint": ("constraint", "blocked", "depends", "cannot", "limit", "dependency", "legal"),
}
TEXT_MARKERS = ("TODO", "FIXME", "XXX", "HACK")
SCAN_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json"}
SKIP_DIRS = {
    ".cache",
    ".claude",
    ".agentic-cadence",
    ".codex-cadence",
    ".codex-run",
    ".codex-transmission",
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".superpowers",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "cache",
    "dist",
    "env",
    "htmlcov",
    "node_modules",
    "venv",
}
MERGE_BLOCKER_SOURCES = {"git_status", "known_failure", "pr_check_failure", "review_finding"}
HYBRID_PRODUCT_EVOLUTION_SOURCES = {BUSINESS_MEMORY_SOURCE, "agent_proposal"}
HYBRID_BLOCKED_BY_MERGE_BLOCKER_SOURCES = HYBRID_PRODUCT_EVOLUTION_SOURCES
MARKDOWN_MARKER_PREFIXES = ("- [ ] ", "- [x] ", "- ", "* ", "+ ", "> ")
MARKER_DELIMITERS = {"", " ", ":", "-", "(", "[", "\t"}
HASH_COMMENT_SUFFIXES = {".toml", ".yaml", ".yml"}
PROSE_SUFFIXES = {".md", ".txt"}
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
UTF32_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
INTENT_SCORE_ADJUSTMENTS = {
    "merge_readiness": {
        "known_failure": 10,
        "pr_check_failure": 10,
        "review_finding": 10,
        "git_status": 5,
        "text_marker": -15,
        "agent_proposal": -25,
        "business_memory": -25,
    },
    "repo_health": {
        "known_failure": -45,
        "pr_check_failure": -45,
        "review_finding": -35,
        "git_status": -15,
        "text_marker": 65,
        "agent_proposal": -30,
        "business_memory": 15,
    },
    "product_evolution": {
        "known_failure": -35,
        "pr_check_failure": -35,
        "review_finding": -25,
        "git_status": -20,
        "text_marker": 10,
        "agent_proposal": 25,
        "business_memory": 15,
    },
    "hybrid": {
        "known_failure": 10,
        "pr_check_failure": 10,
        "review_finding": 10,
        "git_status": 5,
        "text_marker": 0,
        "agent_proposal": 0,
        "business_memory": 5,
    },
}
CANDIDATE_RESERVED_FIELDS = {
    "id",
    "title",
    "task_type",
    "bucket",
    "score",
    "drivers",
    "uncertainty",
    "dependency_fan_out",
    "source",
    "fingerprint",
    "relationships",
    "risk_surface",
    "evidence",
}


@dataclass(frozen=True)
class CandidateBudget:
    max_candidates: int = 25
    max_candidates_per_source: int = 10
    max_text_marker_candidates: int = 10
    max_doc_marker_candidates: int = 5
    max_proposals: int = 3
    max_product_evolution_candidates_in_hybrid: int = 1
    max_business_memory_candidates: int = 5


def _require_non_negative_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def validate_budget(budget: CandidateBudget) -> CandidateBudget:
    for field in budget.__dataclass_fields__:
        _require_non_negative_int(field, getattr(budget, field))
    return budget


def default_relationships() -> dict[str, Any]:
    return {
        "depends_on": [],
        "overlaps": [],
        "supersedes": [],
        "mutually_exclusive_with": [],
        "decomposes_from": None,
    }


def candidate_record(
    *,
    candidate_id: str,
    title: str,
    task_type: str,
    bucket: str,
    score: int,
    drivers: list[str],
    uncertainty: str,
    dependency_fan_out: str,
    source: str,
    fingerprint: str,
    risk_surface: list[str],
    evidence: dict[str, Any],
    relationships: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if task_type not in {"execution", "discovery"}:
        raise ValueError("candidate task_type must be execution or discovery")
    if uncertainty not in RUN_SIGNAL_VALUES:
        raise ValueError("candidate uncertainty must be low, medium, or high")
    if dependency_fan_out not in DEPENDENCY_FAN_OUT_VALUES:
        raise ValueError("candidate dependency_fan_out must be low, medium, or high")
    if extra:
        for field in extra:
            if field in CANDIDATE_RESERVED_FIELDS:
                raise ValueError(f"candidate extra cannot override reserved field: {field}")
    candidate = {
        "id": candidate_id,
        "title": title,
        "task_type": task_type,
        "bucket": bucket,
        "score": score,
        "drivers": list(drivers),
        "uncertainty": uncertainty,
        "dependency_fan_out": dependency_fan_out,
        "source": source,
        "fingerprint": fingerprint,
        "relationships": deepcopy(relationships) if relationships else default_relationships(),
        "risk_surface": list(risk_surface),
        "evidence": dict(evidence),
    }
    if extra:
        candidate.update(extra)
    return candidate


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git exited {result.returncode}"
        raise RuntimeError(detail)
    return result.stdout


def run_git_bytes(cwd: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (
            result.stderr.decode(errors="replace").strip()
            or result.stdout.decode(errors="replace").strip()
            or f"git exited {result.returncode}"
        )
        raise RuntimeError(detail)
    return result.stdout


def fingerprint_component(value: str) -> str:
    return value.replace("\\", "/")


def risk_surface_for_path(path: str) -> str:
    first_segment = path.replace("\\", "/").split("/", 1)[0]
    return first_segment or "repo"


def bounded_score(score: int | float) -> int:
    return max(0, min(100, int(score)))


def apply_intent_scoring(candidates: list[dict[str, Any]], intent: str) -> list[dict[str, Any]]:
    adjustments = INTENT_SCORE_ADJUSTMENTS[intent]
    scored = []
    for candidate in candidates:
        copied = deepcopy(candidate)
        adjustment = adjustments.get(copied["source"], 0)
        copied["score"] = bounded_score(copied.get("score", 0) + adjustment)
        scored.append(copied)
    return scored


def is_path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def repo_local_regular_file_warning(relative_path: str) -> str:
    return f"business memory file {relative_path} must be a repo-local regular file"


def business_memory_decode_warning(relative_path: str, exc: UnicodeError) -> str:
    return f"could not decode business memory file {relative_path}: {exc}"


def business_memory_dirty_fallback_warning(relative_path: str) -> str:
    return f"business memory file {relative_path} has working tree changes and cannot be read securely on this platform"


def business_memory_untracked_fallback_warning(relative_path: str) -> str:
    return f"business memory file {relative_path} is untracked and cannot be read securely on this platform"


def business_memory_index_fallback_warning(relative_path: str) -> str:
    return f"business memory file {relative_path} must resolve to a single tracked regular file"


def checked_business_memory_index_entry(root: Path, relative_path: str) -> tuple[str | None, str | None]:
    try:
        status = run_git(root, "--no-optional-locks", "status", "--porcelain", "--", relative_path)
        if status.strip():
            if any(line.startswith("??") for line in status.splitlines()):
                return None, business_memory_untracked_fallback_warning(relative_path)
            return None, business_memory_dirty_fallback_warning(relative_path)

        entries = [line for line in run_git(root, "ls-files", "--stage", "--", relative_path).splitlines() if line.strip()]
    except RuntimeError as exc:
        return None, f"could not read business memory file {relative_path}: {exc}"

    if not entries:
        return None, None
    if len(entries) != 1:
        return None, business_memory_index_fallback_warning(relative_path)
    mode = entries[0].split(maxsplit=1)[0]
    if mode not in {"100644", "100755"}:
        return None, repo_local_regular_file_warning(relative_path)
    return entries[0], None


def read_repo_local_text_from_git_index(root: Path, relative_path: str) -> tuple[str | None, str | None]:
    try:
        data = run_git_bytes(root, "show", f":{relative_path}")
    except RuntimeError as exc:
        return None, f"could not read business memory file {relative_path}: {exc}"

    try:
        return data.decode("utf-8-sig"), None
    except UnicodeError as exc:
        return None, business_memory_decode_warning(relative_path, exc)


def read_repo_local_text(_path: Path, root: Path, relative_path: str) -> tuple[str | None, str | None]:
    _index_entry, index_warning = checked_business_memory_index_entry(root, relative_path)
    if _index_entry is None:
        return None, index_warning
    return read_repo_local_text_from_git_index(root, relative_path)


def repo_relative_path(value: str, cwd: Path) -> str | None:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        return None
    parsed = PurePosixPath(normalized)
    if not parsed.parts or any(part == ".." for part in parsed.parts):
        return None
    relative = parsed.as_posix()
    if not is_path_under(cwd / relative, cwd):
        return None
    return relative


def git_top_level(cwd: Path) -> Path:
    return Path(run_git(cwd, "rev-parse", "--show-toplevel").strip()).resolve()


def git_status_candidates(cwd: Path, start_index: int = 1, repo_root: Path | None = None) -> list[dict[str, Any]]:
    candidates = []
    repo_root = repo_root or git_top_level(cwd)
    status = run_git(repo_root, "--no-optional-locks", "status", "--porcelain")
    for offset, line in enumerate(status.splitlines(), start=start_index):
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        normalized_path = repo_relative_path(path, repo_root)
        if normalized_path is None:
            continue
        path = normalized_path
        candidates.append(
            candidate_record(
                candidate_id=f"git-status-{offset:03d}",
                title=f"Resolve dirty worktree file: {path}",
                task_type="execution",
                bucket="S",
                score=80,
                drivers=["worktree_hygiene"],
                uncertainty="low",
                dependency_fan_out="low",
                source="git_status",
                fingerprint=f"git-status:{fingerprint_component(path)}",
                risk_surface=[risk_surface_for_path(path)],
                evidence={"path": path, "status": line[:2]},
            )
        )
    return candidates


def known_failure_candidates(known_failures: list[str]) -> list[dict[str, Any]]:
    candidates = []
    for index, failure in enumerate(known_failures, start=1):
        candidates.append(
            candidate_record(
                candidate_id=f"known-failure-{index:03d}",
                title=f"Resolve failing check: {failure}",
                task_type="execution",
                bucket="S",
                score=90,
                drivers=["ci_verification"],
                uncertainty="low",
                dependency_fan_out="low",
                source="known_failure",
                fingerprint=f"known-failure:{fingerprint_component(failure)}",
                risk_surface=["ci"],
                evidence={"failure": failure},
            )
        )
    return candidates


def pr_check_failure_candidates(path: Path, *, start_index: int = 1) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        pr = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        return [], [f"could not read PR JSON file {path}: {exc}"]
    except json.JSONDecodeError as exc:
        return [], [f"could not parse PR JSON file {path}: {exc}"]
    if not isinstance(pr, dict):
        return [], [f"PR JSON file {path} must contain a JSON object"]

    candidates = []
    for index, finding in enumerate(pr_check_failure_findings(pr), start=start_index):
        check = finding["check"]
        state = finding["state"]
        evidence = {
            "id": finding["id"],
            "check": check,
            "state": state,
            "workflow": finding.get("workflow", ""),
            "source": finding.get("source", "status_check_rollup"),
        }
        if finding.get("url"):
            evidence["url"] = finding["url"]
        candidates.append(
            candidate_record(
                candidate_id=f"pr-check-failure-{index:03d}",
                title=f"Resolve failing PR check: {check}",
                task_type="execution",
                bucket="S",
                score=90,
                drivers=["ci_verification"],
                uncertainty="low",
                dependency_fan_out="low",
                source="pr_check_failure",
                fingerprint=f"pr-check-failure:{finding['id']}:{fingerprint_component(check)}:{state}",
                risk_surface=["ci"],
                evidence=evidence,
            )
        )
    return candidates, []


NON_ACTIONABLE_REVIEW_MARKERS = (
    "<!-- walkthrough_start -->",
    "<!-- tips_start -->",
    "<!-- internal state start -->",
)
NON_ACTIONABLE_REVIEW_HEADINGS = (
    "## walkthrough",
    "## tips",
)
NON_ACTIONABLE_REVIEW_BODIES = (
    "approved",
    "lgtm",
    "looks good",
    "no actionable",
    "no actionable comments",
    "no actionable findings",
    "no changes requested",
    "review completed",
)


def review_body_label(body: str) -> str:
    return re.sub(r"\s+", " ", body.strip().lower()).strip(" .!")


def actionable_review_body(body: Any) -> str | None:
    if not isinstance(body, str):
        return None
    stripped = body.strip()
    if not stripped:
        return None
    lowered = stripped.lower()
    if review_body_label(stripped) in NON_ACTIONABLE_REVIEW_BODIES:
        return None
    if any(marker in lowered for marker in NON_ACTIONABLE_REVIEW_MARKERS):
        return None
    first_line = next((line.strip().lower() for line in stripped.splitlines() if line.strip()), "")
    if any(first_line.startswith(heading) for heading in NON_ACTIONABLE_REVIEW_HEADINGS):
        return None
    return stripped


def review_finding_candidate_items(
    findings: list[Any],
    cwd: Path,
    *,
    start_index: int = 1,
) -> tuple[list[dict[str, Any]], list[str], int]:
    warnings = []
    candidates = []
    for index, finding in enumerate(findings, start=start_index):
        if not isinstance(finding, dict):
            warnings.append(f"review finding {index} is not an object")
            continue
        finding_id = finding.get("id")
        finding_file = finding.get("file")
        body = finding.get("body")
        if not finding_id or not finding_file:
            warnings.append(f"review finding {index} is missing id or file")
            continue
        if not isinstance(finding_file, str):
            warnings.append(f"review finding {index} file must be a string")
            continue
        normalized_file = repo_relative_path(finding_file, cwd)
        if normalized_file is None:
            warnings.append(f"review finding {index} file must be repo-relative")
            continue
        title = body or f"Address review finding {finding_id}"
        evidence = {
            "id": str(finding_id),
            "file": normalized_file,
        }
        if "line" in finding:
            evidence["line"] = finding["line"]
        if body:
            evidence["body"] = str(body)
        for optional_field in ("thread_id", "author", "source"):
            if finding.get(optional_field):
                evidence[optional_field] = str(finding[optional_field])
        finding_line = str(finding.get("line", "unknown-line"))
        candidates.append(
            candidate_record(
                candidate_id=f"review-finding-{index:03d}",
                title=f"Address review finding: {title}",
                task_type="execution",
                bucket="S",
                score=88,
                drivers=["reviewer_feedback"],
                uncertainty="low",
                dependency_fan_out="low",
                source="review_finding",
                fingerprint=f"review-finding:{finding_id}:{normalized_file}:{finding_line}",
                risk_surface=[risk_surface_for_path(normalized_file)],
                evidence=evidence,
            )
        )
    return candidates, warnings, start_index + len(findings)


def review_finding_candidates(path: Path, cwd: Path, *, start_index: int = 1) -> tuple[list[dict[str, Any]], list[str], int]:
    try:
        findings = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        return [], [f"could not read review findings file {path}: {exc}"], start_index
    except json.JSONDecodeError as exc:
        return [], [f"could not parse review findings file {path}: {exc}"], start_index

    if not isinstance(findings, list):
        return [], [f"review findings file {path} must contain a JSON list"], start_index

    return review_finding_candidate_items(findings, cwd, start_index=start_index)


def review_threads_nodes(payload: Any) -> list[Any] | None:
    if not isinstance(payload, dict):
        return None
    current: Any = payload
    for key in ("data", "repository", "pullRequest", "reviewThreads", "nodes"):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if isinstance(current, list):
        return current
    return None


def review_thread_comment_nodes(thread: dict[str, Any]) -> list[Any]:
    comments = thread.get("comments")
    if isinstance(comments, dict) and isinstance(comments.get("nodes"), list):
        return comments["nodes"]
    if isinstance(comments, list):
        return comments
    return []


def review_thread_author(comment: dict[str, Any]) -> str | None:
    author = comment.get("author") or comment.get("user")
    if isinstance(author, dict) and author.get("login"):
        return str(author["login"])
    return None


def review_thread_is_current(thread: dict[str, Any], index: int, warnings: list[str]) -> bool:
    is_resolved = thread.get("isResolved")
    is_outdated = thread.get("isOutdated")
    if not isinstance(is_resolved, bool) or not isinstance(is_outdated, bool):
        warnings.append(f"review thread {index} missing isResolved or isOutdated status")
        return False
    return not is_resolved and not is_outdated


def review_thread_findings(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        return [], [f"could not read review threads file {path}: {exc}"]
    except json.JSONDecodeError as exc:
        return [], [f"could not parse review threads file {path}: {exc}"]

    nodes = review_threads_nodes(payload)
    if nodes is None:
        return [], [f"review threads file {path} must contain a GitHub reviewThreads JSON object"]

    warnings = []
    findings: list[dict[str, Any]] = []
    for index, thread in enumerate(nodes, start=1):
        if not isinstance(thread, dict):
            warnings.append(f"review thread {index} is not an object")
            continue
        if not review_thread_is_current(thread, index, warnings):
            continue
        thread_id = str(thread.get("id") or f"thread-{index}")
        thread_file = thread.get("path")
        thread_line = thread.get("line") or thread.get("originalLine")
        for comment_index, comment in enumerate(review_thread_comment_nodes(thread), start=1):
            if not isinstance(comment, dict):
                continue
            comment_outdated = comment.get("outdated")
            if not isinstance(comment_outdated, bool):
                warnings.append(f"review thread {index} comment {comment_index} missing outdated status")
                continue
            if comment_outdated:
                continue
            body = actionable_review_body(comment.get("body"))
            if body is None:
                continue
            finding_file = comment.get("path") or thread_file
            finding_id = comment.get("id") or thread_id
            finding: dict[str, Any] = {
                "id": str(finding_id),
                "file": finding_file,
                "body": body,
                "thread_id": thread_id,
                "source": "review_thread",
            }
            finding_line = comment.get("line") or comment.get("originalLine") or thread_line
            if finding_line is not None:
                finding["line"] = finding_line
            author = review_thread_author(comment)
            if author is not None:
                finding["author"] = author
            findings.append(finding)
    return findings, warnings


def review_thread_candidates(path: Path, cwd: Path, *, start_index: int = 1) -> tuple[list[dict[str, Any]], list[str]]:
    findings, warnings = review_thread_findings(path)
    candidates, candidate_warnings, _next_index = review_finding_candidate_items(findings, cwd, start_index=start_index)
    return candidates, warnings + candidate_warnings


def slug(value: str) -> str:
    parts = []
    previous_was_separator = False
    for character in value.lower():
        if character.isalnum():
            parts.append(character)
            previous_was_separator = False
            continue
        if not previous_was_separator:
            parts.append("-")
            previous_was_separator = True
    return "".join(parts).strip("-") or "unknown"


def contains_semantic_term(text: str, term: str) -> bool:
    phrase = r"\s+".join(re.escape(part) for part in term.strip().split())
    if not phrase:
        return False
    pattern = rf"(?<![A-Za-z0-9_]){phrase}(?![A-Za-z0-9_])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def strip_markdown_bullet(value: str) -> str:
    stripped = value.strip()
    for marker in ("- ", "* ", "+ "):
        if stripped.startswith(marker):
            return stripped[len(marker) :].strip()
    return stripped


def business_memory_heading(line: str) -> str | None:
    match = re.match(r"^[ ]{0,3}#{2,6}[ \t]+(.+?)\s*$", line)
    if match is None:
        return None
    heading = re.sub(r"[ \t]+#+\s*$", "", match.group(1)).strip()
    return heading or None


def blank_business_memory_entry(heading: str, line: int, relative_path: str) -> dict[str, Any]:
    return {
        "heading": heading,
        "line": line,
        "path": relative_path,
        "kind": None,
        "kind_explicit": False,
        "kind_supported": True,
        "status": "active",
        "status_explicit": False,
        "status_supported": True,
        "fulfilled_by": None,
        "superseded_by": None,
        "pain": None,
        "workflow": None,
        "workflow_missing": False,
        "time_saved": None,
        "risk": None,
        "risk_explicit": False,
        "risk_valid": False,
        "notes": [],
        "signals": [],
        "do_not": [],
    }


def parse_business_memory_sections(text: str, relative_path: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings = []
    entries = []
    current: dict[str, Any] | None = None
    current_list_label: str | None = None
    supported_labels = {
        "kind": "kind",
        "status": "status",
        "fulfilled by": "fulfilled_by",
        "superseded by": "superseded_by",
        "pain": "pain",
        "workflow": "workflow",
        "time saved": "time_saved",
        "risk": "risk",
        "notes": "notes",
        "signals": "signals",
        "do not": "do_not",
    }

    def finalize(entry: dict[str, Any] | None) -> None:
        if entry is None:
            return
        meaningful = any(
            entry.get(field)
            for field in ("kind", "pain", "workflow", "time_saved", "risk", "notes", "signals", "do_not")
        )
        meaningful = (
            meaningful
            or entry.get("status_explicit")
            or entry.get("fulfilled_by")
            or entry.get("superseded_by")
        )
        if not meaningful:
            warnings.append(f"business memory entry '{entry['heading']}' has no meaningful fields")
            return

        if entry["fulfilled_by"] is not None:
            entry["fulfilled_by"] = str(entry["fulfilled_by"]).strip() or None
        if entry["superseded_by"] is not None:
            entry["superseded_by"] = str(entry["superseded_by"]).strip() or None

        if entry.get("status_explicit"):
            raw_status = "" if entry.get("status") is None else str(entry.get("status")).strip()
            if not raw_status:
                warnings.append(f"business memory entry '{entry['heading']}' has empty Status")
                entry["status"] = "invalid"
                entry["status_supported"] = False
                entries.append(entry)
                return
            status = raw_status.lower().replace(" ", "_").replace("-", "_")
            if status not in BUSINESS_MEMORY_STATUSES:
                warnings.append(f"business memory entry '{entry['heading']}' has unsupported Status: {entry['status']}")
                entry["status"] = "invalid"
                entry["status_supported"] = False
                entries.append(entry)
                return
            entry["status"] = status
        else:
            if entry["superseded_by"] is not None:
                status = "superseded"
            elif entry["fulfilled_by"] is not None:
                status = "fulfilled"
            else:
                status = "active"
            entry["status"] = status

        if status in {"fulfilled", "superseded"}:
            entries.append(entry)
            return

        if entry["kind"] is not None:
            entry["kind_explicit"] = True
            normalized_kind = str(entry["kind"]).strip().lower().replace(" ", "_").replace("-", "_")
            if normalized_kind in BUSINESS_MEMORY_CLASSIFICATIONS:
                entry["kind"] = normalized_kind
                entry["kind_supported"] = True
            else:
                warnings.append(f"business memory entry '{entry['heading']}' has unsupported Kind: {entry['kind']}")
                entry["kind"] = "unknown"
                entry["kind_supported"] = False

        if entry["time_saved"] is None:
            entry["time_saved"] = "medium"
        else:
            time_saved = str(entry["time_saved"]).strip().lower()
            if time_saved not in BUSINESS_VALUE_VALUES:
                warnings.append(
                    f"business memory entry '{entry['heading']}' has invalid Time Saved: {entry['time_saved']}"
                )
                time_saved = "medium"
            entry["time_saved"] = time_saved

        if entry["risk"] is None:
            entry["risk"] = "medium"
        else:
            risk = str(entry["risk"]).strip().lower()
            if risk not in BUSINESS_VALUE_VALUES:
                warnings.append(f"business memory entry '{entry['heading']}' has invalid Risk: {entry['risk']}")
                risk = "medium"
            else:
                entry["risk_valid"] = True
            entry["risk"] = risk

        if entry["workflow"] is None:
            warnings.append(
                f"business memory entry '{entry['heading']}' missing Workflow; preserving as unclassified signal"
            )
            entry["workflow_missing"] = True
            entry["workflow"] = "unknown"
        else:
            entry["workflow"] = str(entry["workflow"]).strip() or "unknown"
        entries.append(entry)

    for line_number, line in enumerate(text.splitlines(), start=1):
        heading = business_memory_heading(line)
        if heading is not None:
            finalize(current)
            current = blank_business_memory_entry(heading, line_number, relative_path)
            current_list_label = None
            continue
        if current is None:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        label, separator, value = stripped.partition(":")
        normalized_label = label.strip().lower()
        if separator and normalized_label in supported_labels:
            field = supported_labels[normalized_label]
            value = value.strip()
            if field in {"notes", "signals", "do_not"}:
                current_list_label = field
                if value:
                    current[field].append(strip_markdown_bullet(value))
                continue
            if field == "risk":
                current["risk_explicit"] = True
            if field == "status":
                current["status_explicit"] = True
            current[field] = value or None
            current_list_label = None
            continue
        if current_list_label in {"notes", "signals", "do_not"} and stripped.startswith(("- ", "* ", "+ ")):
            current[current_list_label].append(strip_markdown_bullet(stripped))
            continue
        warnings.append(
            f"malformed business memory line {relative_path}:{line_number} in entry '{current['heading']}': {stripped}"
        )
        current_list_label = None

    finalize(current)
    return entries, warnings


def classify_business_memory_entry(entry: dict[str, Any]) -> tuple[str, str]:
    if entry.get("workflow_missing"):
        return "unknown", "low"

    if entry.get("kind_explicit"):
        if not entry.get("kind_supported", True):
            return "unknown", "low"
        kind = str(entry.get("kind") or "unknown")
        if kind == "unknown":
            return "unknown", "low"
        if kind in BUSINESS_MEMORY_CLASSIFICATIONS:
            return kind, "high"

    if entry.get("risk") == "high":
        return "risk", "medium"

    combined_parts = [
        entry.get("heading"),
        entry.get("pain"),
        entry.get("workflow"),
        entry.get("risk"),
        *(entry.get("notes") or []),
        *(entry.get("do_not") or []),
        *(entry.get("signals") or []),
    ]
    combined = " ".join(str(part).lower() for part in combined_parts if part)
    for classification, terms in BUSINESS_MEMORY_CLASSIFICATION_TERMS.items():
        if any(contains_semantic_term(combined, term) for term in terms):
            return classification, "medium"
    return "unknown", "low"


def business_memory_repo_health_text(entry: dict[str, Any]) -> str:
    combined_parts = [
        entry.get("heading"),
        entry.get("pain"),
        entry.get("workflow"),
        *(entry.get("notes") or []),
        *(entry.get("do_not") or []),
        *(entry.get("signals") or []),
    ]
    return " ".join(str(part).lower() for part in combined_parts if part)


def business_memory_has_repo_health_term(entry: dict[str, Any]) -> bool:
    combined = business_memory_repo_health_text(entry)
    return any(contains_semantic_term(combined, term) for term in BUSINESS_MEMORY_REPO_HEALTH_WORKFLOW_TERMS)


def business_memory_allowed_for_intent(intent: str, entry: dict[str, Any]) -> bool:
    if entry.get("status") != "active":
        return False
    if intent == "merge_readiness":
        return False
    if intent in {"product_evolution", "hybrid"}:
        return True
    if intent != "repo_health":
        return False

    return business_memory_has_repo_health_term(entry)


def business_memory_candidates(cwd: Path, intent: str, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    if intent == "merge_readiness" or limit <= 0:
        return [], []
    path = cwd / BUSINESS_MEMORY_PATH
    relative_path = BUSINESS_MEMORY_PATH.as_posix()
    text, read_warning = read_repo_local_text(path, cwd, relative_path)
    if text is None and read_warning is None:
        return [], []
    if text is None:
        return [], [read_warning]

    entries, warnings = parse_business_memory_sections(text, relative_path)
    candidates = []
    for entry in entries:
        if not business_memory_allowed_for_intent(intent, entry):
            continue
        classification, confidence = classify_business_memory_entry(entry)
        workflow = entry.get("workflow") or "unknown"
        workflow_label = "repo area" if workflow == "unknown" else workflow
        done_criteria = [
            "classify the signal" if classification == "unknown" else f"confirm {classification} classification",
            f"identify affected {workflow_label} files",
            "identify tests or missing tests",
            "produce one bounded implementation candidate",
        ]
        drivers = ["unknown_repo_area"]
        if classification == "unknown":
            drivers.append("unclassified_signal")
        score = BUSINESS_MEMORY_TIME_SAVED_SCORE[entry["time_saved"]] + BUSINESS_MEMORY_RISK_BONUS[entry["risk"]]
        if classification in {"business_rule", "risk"}:
            score += 10
        elif classification == "nice_to_have":
            score -= 15
        elif classification == "unknown":
            score -= 10
        notes = entry.get("notes") or None
        candidates.append(
            candidate_record(
                candidate_id=f"business-memory-{len(candidates) + 1:03d}",
                title=f"Ground {entry['heading']} into an executable repo slice",
                task_type="discovery",
                bucket="S",
                score=score,
                drivers=drivers,
                uncertainty="medium" if confidence != "low" else "high",
                dependency_fan_out="medium",
                source=BUSINESS_MEMORY_SOURCE,
                fingerprint=f"business-memory:{slug(entry['heading'])}:{entry['line']}",
                risk_surface=[slug(workflow)],
                evidence={
                    "path": relative_path,
                    "heading": entry["heading"],
                    "line": entry["line"],
                    "pain": entry.get("pain"),
                    "notes": notes,
                    "signals": entry.get("signals") or [],
                    "risk": entry["risk"],
                },
                extra={
                    "maturity": "discovery",
                    "classification": classification,
                    "classification_confidence": confidence,
                    "business_value": entry["time_saved"],
                    "workflow": workflow,
                    "repo_anchors": [],
                    "done_criteria": done_criteria,
                    "guardrails": entry.get("do_not") or [],
                },
            )
        )
    ranked_candidates = sorted(
        enumerate(candidates),
        key=lambda item: (item[1].get("score", 0), -item[0]),
        reverse=True,
    )
    return [candidate for _index, candidate in ranked_candidates[:limit]], warnings


def iter_scannable_files(cwd: Path):
    for root, dirs, files in cwd.walk():
        dirs[:] = sorted(directory for directory in dirs if directory not in SKIP_DIRS)
        for name in sorted(files):
            path = root / name
            if path.is_symlink() or not is_path_under(path, cwd):
                continue
            if path.suffix.lower() in SCAN_SUFFIXES:
                yield path


def marker_at_start(text: str) -> str | None:
    stripped = text.strip()
    for marker in TEXT_MARKERS:
        if not stripped.startswith(marker):
            continue
        delimiter = stripped[len(marker) : len(marker) + 1]
        if delimiter in MARKER_DELIMITERS:
            return marker
    return None


def marker_from_prefixed_comment(line: str, prefix: str) -> str | None:
    stripped = line.lstrip()
    if not stripped.startswith(prefix):
        return None
    return marker_at_start(stripped[len(prefix) :])


def python_marker_hits(lines: list[str]):
    source = "\n".join(lines)
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            marker = marker_from_prefixed_comment(token.string, "#")
            if marker is not None:
                yield token.start[0], marker
    except tokenize.TokenError:
        for line_number, line in enumerate(lines, start=1):
            marker = marker_from_prefixed_comment(line, "#")
            if marker is not None:
                yield line_number, marker


def prose_marker_hits(lines: list[str]):
    in_fenced_block = False
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fenced_block = not in_fenced_block
            continue
        if in_fenced_block:
            continue
        candidate = stripped
        for prefix in MARKDOWN_MARKER_PREFIXES:
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix) :].lstrip()
                break
        if candidate.startswith("<!--"):
            candidate = candidate.removeprefix("<!--").lstrip()
        marker = marker_at_start(candidate)
        if marker is not None:
            yield line_number, marker


def hash_comment_marker_hits(lines: list[str]):
    for line_number, line in enumerate(lines, start=1):
        marker = marker_from_prefixed_comment(line, "#")
        if marker is not None:
            yield line_number, marker


def marker_hits_for_file(path: Path, lines: list[str]):
    suffix = path.suffix.lower()
    if suffix == ".py":
        yield from python_marker_hits(lines)
    elif suffix in PROSE_SUFFIXES:
        yield from prose_marker_hits(lines)
    elif suffix in HASH_COMMENT_SUFFIXES:
        yield from hash_comment_marker_hits(lines)


def read_scannable_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        if raw.startswith(UTF32_BOMS):
            raise
        if raw.startswith(UTF16_BOMS):
            return raw.decode("utf-16")
        raise


def text_marker_candidates(cwd: Path, limit: int, doc_limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    candidates = []
    warnings = []
    doc_candidates = 0
    for path in iter_scannable_files(cwd):
        if len(candidates) >= limit:
            break
        relative = path.relative_to(cwd).as_posix()
        try:
            lines = read_scannable_text(path).splitlines()
        except UnicodeError:
            warnings.append(f"could not scan non-UTF8 file {relative}")
            continue
        except OSError as exc:
            warnings.append(f"could not scan file {relative}: {exc}")
            continue
        for line_number, marker in marker_hits_for_file(path, lines):
            if len(candidates) >= limit:
                break
            if relative.startswith("docs/"):
                if doc_candidates >= doc_limit:
                    continue
                doc_candidates += 1
            line = lines[line_number - 1]
            candidates.append(
                candidate_record(
                    candidate_id=f"text-marker-{len(candidates) + 1:03d}",
                    title=f"Review {marker} marker in {relative}:{line_number}",
                    task_type="discovery",
                    bucket="S",
                    score=35,
                    drivers=["unknown_repo_area"],
                    uncertainty="medium",
                    dependency_fan_out="low",
                    source="text_marker",
                    fingerprint=f"text-marker:{relative}:{line_number}",
                    risk_surface=[risk_surface_for_path(relative)],
                    evidence={
                        "path": relative,
                        "line": line_number,
                        "marker": marker,
                        "text": line.strip(),
                    },
                )
            )
    return candidates, warnings


def deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated = []
    by_fingerprint = {}
    for candidate in candidates:
        fingerprint = candidate["fingerprint"]
        if fingerprint not in by_fingerprint:
            candidate = deepcopy(candidate)
            candidate["evidence"]["occurrences"] = 1
            by_fingerprint[fingerprint] = candidate
            deduplicated.append(candidate)
            continue
        existing = by_fingerprint[fingerprint]
        existing["score"] = max(existing["score"], candidate["score"])
        existing["evidence"]["occurrences"] = existing["evidence"].get("occurrences", 1) + 1
    return deduplicated


def apply_budget(candidates: list[dict[str, Any]], budget: CandidateBudget) -> list[dict[str, Any]]:
    selected = []
    source_counts: dict[str, int] = {}
    ranked_candidates = sorted(enumerate(candidates), key=lambda item: (item[1].get("score", 0), -item[0]), reverse=True)
    for _index, candidate in ranked_candidates:
        source = candidate["source"]
        if len(selected) >= budget.max_candidates:
            break
        source_limit = budget.max_candidates_per_source
        if source == "text_marker":
            source_limit = min(source_limit, budget.max_text_marker_candidates)
        if source == BUSINESS_MEMORY_SOURCE:
            source_limit = min(source_limit, budget.max_business_memory_candidates)
        if source_counts.get(source, 0) >= source_limit:
            continue
        selected.append(candidate)
        source_counts[source] = source_counts.get(source, 0) + 1
    return selected


def proposal_candidates(intent: str, proposal_allowance: str, budget: CandidateBudget) -> list[dict[str, Any]]:
    if proposal_allowance == "none":
        return []
    if intent not in {"product_evolution", "hybrid"}:
        return []
    if intent == "hybrid" and budget.max_product_evolution_candidates_in_hybrid <= 0:
        return []
    if budget.max_proposals <= 0:
        return []
    executable = proposal_allowance == "elect"
    return [
        candidate_record(
            candidate_id="agent-proposal-001",
            title="Explore the next repo capability from local signals",
            task_type="discovery",
            bucket="S",
            score=65 if intent == "product_evolution" else 20,
            drivers=["unknown_repo_area"],
            uncertainty="high",
            dependency_fan_out="medium",
            source="agent_proposal",
            fingerprint="agent-proposal:local-capability-discovery",
            risk_surface=["repo"],
            evidence={"intent": intent, "rationale": "proposal allowance enabled for this discovery run"},
            extra={
                "requires_user_allowance": True,
                "allowance": proposal_allowance,
                "executable": executable,
                "allowance_reason": "not directly required by current repo evidence",
            },
        )
    ]


def election_pool(
    candidates: list[dict[str, Any]],
    intent: str,
    proposal_allowance: str,
    budget: CandidateBudget,
) -> tuple[list[dict[str, Any]], str]:
    has_merge_blocker = any(candidate["source"] in MERGE_BLOCKER_SOURCES for candidate in candidates)
    pool = []
    intent_drift = "none"
    hybrid_product_evolution_count = 0
    for candidate in candidates:
        if candidate["source"] == "agent_proposal" and proposal_allowance != "elect":
            continue
        if (
            intent == "hybrid"
            and has_merge_blocker
            and candidate["source"] in HYBRID_BLOCKED_BY_MERGE_BLOCKER_SOURCES
        ):
            intent_drift = "blocked"
            continue
        if intent == "hybrid" and candidate["source"] in HYBRID_PRODUCT_EVOLUTION_SOURCES:
            if hybrid_product_evolution_count >= budget.max_product_evolution_candidates_in_hybrid:
                continue
            hybrid_product_evolution_count += 1
        pool.append(candidate)
    return pool, intent_drift


def run_signals(
    candidates: list[dict[str, Any]],
    repo_confidence: str,
    intent_drift: str,
    raw_candidate_count: int | None = None,
) -> dict[str, str]:
    candidate_count = raw_candidate_count if raw_candidate_count is not None else len(candidates)
    candidate_growth = "low"
    uncertainty = "low"
    if candidate_count > 20:
        candidate_growth = "high"
        uncertainty = "high"
    elif candidate_count > 10:
        candidate_growth = "medium"
        uncertainty = "medium"
    uncertainty_rank = {value: index for index, value in enumerate(RUN_SIGNAL_VALUES)}
    for candidate in candidates:
        candidate_uncertainty = candidate.get("uncertainty")
        if candidate_uncertainty in uncertainty_rank and uncertainty_rank[candidate_uncertainty] > uncertainty_rank[uncertainty]:
            uncertainty = candidate_uncertainty
    return {
        "repo_confidence": repo_confidence,
        "uncertainty": uncertainty,
        "candidate_growth": candidate_growth,
        "intent_drift": intent_drift,
    }


def empty_sources() -> dict[str, Any]:
    return {
        "git_status": False,
        "known_failures": 0,
        "pr_check_failures": 0,
        "review_findings": 0,
        "text_markers": 0,
        "proposals": 0,
        "business_memory": 0,
    }


def discover_candidates(
    *,
    cwd: Path,
    intent: str | None,
    discovery_mode: str = "local",
    proposal_allowance: str = "none",
    known_failures: list[str] | None = None,
    pr_json_file: Path | None = None,
    review_findings_file: Path | None = None,
    review_threads_file: Path | None = None,
    elect: bool = False,
    max_tasks: int = 1,
    budget: CandidateBudget | None = None,
) -> dict[str, Any]:
    if discovery_mode not in DISCOVERY_MODES:
        raise ValueError("discovery_mode must be off, local, or expanded")
    if proposal_allowance not in PROPOSAL_ALLOWANCES:
        raise ValueError("proposal_allowance must be none, surface, or elect")

    sources = empty_sources()
    if discovery_mode == "off":
        return {
            "intent": intent,
            "proposal_allowance": proposal_allowance,
            "discovery_mode": discovery_mode,
            "cwd": str(cwd),
            "candidates": [],
            "elected_next": [],
            "run_signals": run_signals([], "high", "none"),
            "sources": sources,
            "warnings": ["discovery disabled by policy"],
        }
    if discovery_mode == "expanded":
        raise ValueError("expanded discovery mode is reserved for v2")
    if intent not in DISCOVERY_INTENTS:
        raise ValueError("intent must be merge_readiness, repo_health, product_evolution, or hybrid")

    active_budget = validate_budget(budget or CandidateBudget())
    warnings = []
    failures = known_failures or []
    repo_root = git_top_level(cwd)
    failure_candidates = known_failure_candidates(failures)
    check_candidates = []
    if pr_json_file is not None:
        check_candidates, check_warnings = pr_check_failure_candidates(pr_json_file)
        warnings.extend(check_warnings)
    status_candidates = git_status_candidates(cwd, start_index=len(failure_candidates) + 1, repo_root=repo_root)
    review_candidates = []
    next_review_finding_index = 1
    if review_findings_file is not None:
        review_candidates, review_warnings, next_review_finding_index = review_finding_candidates(review_findings_file, repo_root)
        warnings.extend(review_warnings)
    if review_threads_file is not None:
        thread_candidates, thread_warnings = review_thread_candidates(
            review_threads_file,
            repo_root,
            start_index=next_review_finding_index,
        )
        review_candidates.extend(thread_candidates)
        warnings.extend(thread_warnings)
    marker_candidates, marker_warnings = text_marker_candidates(
        repo_root,
        active_budget.max_text_marker_candidates,
        active_budget.max_doc_marker_candidates,
    )
    warnings.extend(marker_warnings)
    memory_candidates, memory_warnings = business_memory_candidates(
        repo_root,
        intent,
        active_budget.max_business_memory_candidates,
    )
    warnings.extend(memory_warnings)
    proposal_items = proposal_candidates(intent, proposal_allowance, active_budget)
    raw_candidates = (
        failure_candidates
        + check_candidates
        + status_candidates
        + review_candidates
        + marker_candidates
        + memory_candidates
        + proposal_items
    )
    candidates = apply_budget(apply_intent_scoring(deduplicate_candidates(raw_candidates), intent), active_budget)
    pool, intent_drift = election_pool(candidates, intent, proposal_allowance, active_budget)
    elected_next = elect_candidates(pool, max_tasks=max_tasks) if elect else []
    sources["known_failures"] = len(failures)
    sources["pr_check_failures"] = sum(1 for candidate in candidates if candidate["source"] == "pr_check_failure")
    sources["git_status"] = True
    sources["review_findings"] = sum(1 for candidate in candidates if candidate["source"] == "review_finding")
    sources["text_markers"] = sum(1 for candidate in candidates if candidate["source"] == "text_marker")
    sources["proposals"] = sum(1 for candidate in candidates if candidate["source"] == "agent_proposal")
    sources["business_memory"] = sum(1 for candidate in candidates if candidate["source"] == BUSINESS_MEMORY_SOURCE)
    repo_confidence = "low" if status_candidates or failure_candidates or check_candidates else "high"

    return {
        "intent": intent,
        "proposal_allowance": proposal_allowance,
        "discovery_mode": discovery_mode,
        "cwd": str(repo_root),
        "candidates": candidates,
        "elected_next": elected_next,
        "run_signals": run_signals(candidates, repo_confidence, intent_drift, raw_candidate_count=len(raw_candidates)),
        "sources": sources,
        "warnings": warnings,
    }
