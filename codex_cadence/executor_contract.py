from __future__ import annotations

import os
import re
import shlex
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.model import BUCKETS, TASK_TYPES
from codex_cadence.repo_state import validate_repo_snapshot
from codex_cadence.store import utc_now

EXECUTOR_TASK_SCHEMA_VERSION = "generic-executor-task.v1"
EXECUTOR_RESULT_SCHEMA_VERSION = "generic-executor-result.v1"
EXECUTOR_STATUSES = ("succeeded", "failed", "blocked", "stopped")
EXECUTOR_CONFIDENCE_VALUES = ("high", "medium", "low")
DEFAULT_EXECUTOR_STOP_CONDITIONS = ["brake_not_drive", "operator_stop", "context_pressure", "timeout"]
_SHELL_COMMANDS = {
    "bash",
    "bash.exe",
    "dash",
    "dash.exe",
    "sh",
    "sh.exe",
    "zsh",
    "zsh.exe",
}
_POWERSHELL_COMMANDS = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
_CMD_COMMANDS = {"cmd", "cmd.exe"}
_GIT_OPTIONS_WITH_VALUE = {
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}
_GIT_OPTIONS_WITH_EQUALS = (
    "-c",
    "--config-env=",
    "--exec-path=",
    "--git-dir=",
    "--namespace=",
    "--super-prefix=",
    "--work-tree=",
)
_GH_OPTIONS_WITH_VALUE = {
    "-r",
    "--config-dir",
    "--editor",
    "--git-protocol",
    "--hostname",
    "--repo",
}
_GH_OPTIONS_WITH_EQUALS = (
    "--config-dir=",
    "--editor=",
    "--git-protocol=",
    "--hostname=",
    "--repo=",
)
_COMMAND_SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "{", "}"}
_COMMAND_EXPANSION_DEPTH_LIMIT = 32
_PARAMETER_EXPANSION_PATTERN = re.compile(r"(?<!\\)\$(?!\()(\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)")
_IFS_EXPANSION_PATTERN = re.compile(r"\$\{ifs\}|\$ifs\b", re.IGNORECASE)
_ENV_OPTIONS_WITH_VALUE = {"-u", "--unset", "-c", "-C", "--chdir", "-s", "-S", "--split-string", "--argv0"}
_ENV_OPTIONS_WITH_EQUALS = ("--unset=", "--chdir=", "--split-string=", "--argv0=")
_SUDO_OPTIONS_WITH_VALUE = {
    "-u",
    "--user",
    "-g",
    "--group",
    "-h",
    "--host",
    "-p",
    "--prompt",
    "-c",
    "-C",
    "--close-from",
    "-T",
    "--command-timeout",
}
_SUDO_OPTIONS_WITH_EQUALS = (
    "--user=",
    "--group=",
    "--host=",
    "--prompt=",
    "--close-from=",
    "--command-timeout=",
)
_EXEC_OPTIONS_WITH_VALUE = {"-a"}
_NICE_OPTIONS_WITH_VALUE = {"-n", "--adjustment"}
_NICE_OPTIONS_WITH_EQUALS = ("--adjustment=",)
_TIME_OPTIONS_WITH_VALUE = {"-f", "--format", "-o", "--output"}
_TIME_OPTIONS_WITH_EQUALS = ("--format=", "--output=")
_TIME_OPTIONS_WITHOUT_VALUE = {"-a", "--append", "-p", "--portability", "-v", "--verbose", "--quiet"}
_TIMEOUT_OPTIONS_WITH_VALUE = {"-k", "--kill-after", "-s", "--signal"}
_TIMEOUT_OPTIONS_WITH_EQUALS = ("--kill-after=", "--signal=")
_TIMEOUT_OPTIONS_WITHOUT_VALUE = {"--preserve-status", "--foreground", "-v", "--verbose"}


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_non_empty_string(item) for item in value)


def _repo_relative_path(value: Any) -> str | None:
    if not _non_empty_string(value):
        return None
    raw = str(value).replace("\\", "/").strip()
    if raw.startswith("/"):
        return None
    normalized = raw.strip("/")
    if not normalized:
        return "."
    if ":" in normalized:
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _normalized_filesystem_path(value: Any) -> str | None:
    if not _non_empty_string(value):
        return None
    if "\0" in value:
        return None
    try:
        path = Path(value)
        if not path.is_absolute():
            return None
        return os.path.normcase(str(path.expanduser().resolve(strict=False)))
    except (OSError, RuntimeError, ValueError):
        return None


def _path_allowed(path: str, allowed_paths: list[str]) -> bool:
    normalized = _repo_relative_path(path)
    if normalized is None:
        return False
    for allowed_path in allowed_paths:
        allowed = _repo_relative_path(allowed_path)
        if allowed == ".":
            return True
        if normalized == allowed or normalized.startswith(f"{allowed}/"):
            return True
    return False


def _normalized_command(command: str) -> str:
    return " ".join(command.strip().lower().split())


def _command_name(token: str) -> str:
    return PurePosixPath(token.replace("\\", "/")).name.lower()


def _python_command_name(command_name: str) -> bool:
    return command_name in {"py", "py.exe"} or re.fullmatch(
        r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?",
        command_name,
    ) is not None


def _command_text_for_lexing(command: str) -> str:
    normalized = command.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\\\n[ \t]*", " ", normalized)
    return normalized.replace("\n", " ; ")


def _command_tokens(command: str, *, lower: bool = True) -> list[str]:
    shell_text = _command_text_for_lexing(command)
    try:
        lexer = shlex.shlex(shell_text, posix=True, punctuation_chars=True)
        lexer.commenters = ""
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = shell_text.strip().split()
    return [token.lower() for token in tokens] if lower else tokens


def _command_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _COMMAND_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _shell_assignment(token: str) -> bool:
    if "=" not in token or token.startswith("-"):
        return False
    name, _value = token.split("=", 1)
    return bool(name) and (name[0].isalpha() or name[0] == "_") and all(
        char.isalnum() or char == "_" for char in name
    )


def _assignment_pair(token: str) -> tuple[str, str] | None:
    if not _shell_assignment(token):
        return None
    name, value = token.split("=", 1)
    return name.lower(), value


def _option_step(
    tokens: list[str],
    index: int,
    options_with_value: set[str],
    options_with_equals: tuple[str, ...],
) -> int | None:
    token = tokens[index]
    if token in options_with_value:
        return min(index + 2, len(tokens))
    if any(token.startswith(prefix) and token != prefix for prefix in options_with_equals):
        return index + 1
    return None


def _strip_shell_dollar_quote(token: str) -> str:
    if token.startswith("$") and len(token) > 1 and not token.startswith("$("):
        return token[1:]
    return token


def _strip_single_quoted_ranges(command: str) -> str:
    result: list[str] = []
    index = 0
    single_quoted = False
    double_quoted = False
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            if not single_quoted:
                result.append(" ")
            escaped = False
            index += 1
            continue
        if char == "\\" and not single_quoted:
            if not single_quoted:
                result.append(" ")
            escaped = True
            index += 1
            continue
        if char == "'" and not double_quoted:
            single_quoted = not single_quoted
            result.append(" ")
            index += 1
            continue
        if char == '"' and not single_quoted:
            double_quoted = not double_quoted
        result.append(" " if single_quoted else char)
        index += 1
    return "".join(result)


def _first_non_assignment_index(tokens: list[str]) -> int | None:
    current = 0
    while current < len(tokens) and _assignment_pair(tokens[current]) is not None:
        current += 1
    return current if current < len(tokens) else None


def _visible_environment_assignments(tokens: list[str], command_index: int) -> dict[str, str]:
    assignments: dict[str, str] = {}
    current = 0
    while current < command_index:
        pair = _assignment_pair(tokens[current])
        if pair is not None:
            assignments[pair[0]] = pair[1]
            current += 1
            continue
        command = _command_name(tokens[current])
        if command == "env":
            current += 1
            while current < command_index:
                pair = _assignment_pair(tokens[current])
                if pair is not None:
                    assignments[pair[0]] = pair[1]
                    current += 1
                    continue
                option_next = _option_step(tokens, current, _ENV_OPTIONS_WITH_VALUE, _ENV_OPTIONS_WITH_EQUALS)
                if option_next is not None:
                    current = option_next
                    continue
                if tokens[current].startswith("-"):
                    current += 1
                    continue
                break
        current += 1
    return assignments


def _first_command_index(tokens: list[str]) -> int | None:
    current = 0
    while current < len(tokens):
        token = tokens[current]
        command = _command_name(token)
        if _shell_assignment(token):
            current += 1
            continue
        if command == "env":
            current += 1
            while current < len(tokens):
                if _assignment_pair(tokens[current]) is not None:
                    current += 1
                    continue
                option_next = _option_step(tokens, current, _ENV_OPTIONS_WITH_VALUE, _ENV_OPTIONS_WITH_EQUALS)
                if option_next is not None:
                    current = option_next
                    continue
                if tokens[current].startswith("-"):
                    current += 1
                    continue
                break
            continue
        if command == "sudo":
            current += 1
            while current < len(tokens):
                option_next = _option_step(tokens, current, _SUDO_OPTIONS_WITH_VALUE, _SUDO_OPTIONS_WITH_EQUALS)
                if option_next is not None:
                    current = option_next
                    continue
                if tokens[current].startswith("-"):
                    current += 1
                    continue
                break
            continue
        if command == "command":
            current += 1
            while current < len(tokens):
                if tokens[current] == "--":
                    current += 1
                    break
                if tokens[current] in {"-p"}:
                    current += 1
                    continue
                if tokens[current] in {"-v", "-V"}:
                    return current - 1
                break
            continue
        if command == "exec":
            current += 1
            while current < len(tokens):
                option_next = _option_step(tokens, current, _EXEC_OPTIONS_WITH_VALUE, ())
                if option_next is not None:
                    current = option_next
                    continue
                if tokens[current] in {"-c", "-l"}:
                    current += 1
                    continue
                break
            continue
        if command == "builtin":
            current += 1
            continue
        if command == "nohup":
            current += 1
            continue
        if command == "nice":
            current += 1
            while current < len(tokens):
                option_next = _option_step(tokens, current, _NICE_OPTIONS_WITH_VALUE, _NICE_OPTIONS_WITH_EQUALS)
                if option_next is not None:
                    current = option_next
                    continue
                if re.fullmatch(r"-\d+", tokens[current]):
                    current += 1
                    continue
                if tokens[current].startswith("-"):
                    current += 1
                    continue
                break
            continue
        if command == "time":
            current += 1
            while current < len(tokens):
                option_next = _option_step(tokens, current, _TIME_OPTIONS_WITH_VALUE, _TIME_OPTIONS_WITH_EQUALS)
                if option_next is not None:
                    current = option_next
                    continue
                if tokens[current] in _TIME_OPTIONS_WITHOUT_VALUE:
                    current += 1
                    continue
                break
            continue
        if command == "timeout":
            current += 1
            while current < len(tokens):
                option_next = _option_step(tokens, current, _TIMEOUT_OPTIONS_WITH_VALUE, _TIMEOUT_OPTIONS_WITH_EQUALS)
                if option_next is not None:
                    current = option_next
                    continue
                if tokens[current] in _TIMEOUT_OPTIONS_WITHOUT_VALUE:
                    current += 1
                    continue
                if tokens[current].startswith("-"):
                    current += 1
                    continue
                break
            if current < len(tokens):
                current += 1
            continue
        return current
    return None


def _git_config_alias(value: str) -> tuple[str, str] | None:
    if not value.startswith("alias.") or "=" not in value:
        return None
    alias_name, alias_value = value.split("=", 1)
    alias_name = alias_name.removeprefix("alias.").strip().lower()
    alias_value = alias_value.strip().lower()
    if not alias_name or not alias_value:
        return None
    return alias_name, alias_value


def _git_config_env_alias(value: str, assignments: dict[str, str]) -> tuple[str, str | None] | None:
    if not value.startswith("alias.") or "=" not in value:
        return None
    alias_name, env_name = value.split("=", 1)
    alias_name = alias_name.removeprefix("alias.").strip().lower()
    env_name = env_name.strip().lower()
    if not alias_name or not env_name:
        return None
    return alias_name, assignments.get(env_name)


def _git_alias_invokes(
    alias_value: str | None,
    subcommand: str,
    aliases: dict[str, str | None],
    seen: set[str] | None = None,
) -> bool:
    if alias_value is None:
        return True
    shell_alias = alias_value.startswith("!")
    alias_command = alias_value.removeprefix("!")
    if shell_alias and _command_invokes(alias_command, f"git {subcommand}"):
        return True
    alias_tokens = _command_tokens(alias_command)
    if not alias_tokens:
        return False
    if _command_name(alias_tokens[0]) in {"git", "git.exe"}:
        return len(alias_tokens) > 1 and alias_tokens[1] == subcommand
    alias_name = alias_tokens[0]
    if alias_name == subcommand:
        return True
    if alias_name in aliases:
        seen_aliases = set() if seen is None else set(seen)
        if alias_name in seen_aliases:
            return False
        seen_aliases.add(alias_name)
        return _git_alias_invokes(aliases[alias_name], subcommand, aliases, seen_aliases)
    return False


def _option_uses_value(token: str, options_with_value: set[str], options_with_equals: tuple[str, ...]) -> bool:
    if token in options_with_value:
        return True
    return any(token.startswith(prefix) and token != prefix for prefix in options_with_equals)


def _next_git_subcommand(tokens: list[str], index: int) -> str | None:
    current = index + 1
    while current < len(tokens):
        token = tokens[current]
        if token == "--":
            current += 1
            break
        if token in _COMMAND_SEPARATORS:
            return None
        if _option_uses_value(token, _GIT_OPTIONS_WITH_VALUE, _GIT_OPTIONS_WITH_EQUALS):
            current += 2 if token in _GIT_OPTIONS_WITH_VALUE else 1
            continue
        if token.startswith("-"):
            current += 1
            continue
        return token
    return tokens[current] if current < len(tokens) else None


def _git_aliases_before_subcommand(tokens: list[str], index: int) -> dict[str, str | None]:
    aliases: dict[str, str | None] = {}
    assignments = _visible_environment_assignments(tokens, index)
    current = index + 1
    while current < len(tokens):
        token = tokens[current]
        if token == "--" or token in _COMMAND_SEPARATORS:
            return aliases
        if token == "-c" and current + 1 < len(tokens):
            alias = _git_config_alias(tokens[current + 1])
            if alias is not None:
                aliases[alias[0]] = alias[1]
            current += 2
            continue
        if token.startswith("-c") and token != "-c":
            alias = _git_config_alias(token[2:])
            if alias is not None:
                aliases[alias[0]] = alias[1]
            current += 1
            continue
        if token == "--config-env" and current + 1 < len(tokens):
            alias = _git_config_env_alias(tokens[current + 1], assignments)
            if alias is not None:
                aliases[alias[0]] = alias[1]
            current += 2
            continue
        if token.startswith("--config-env="):
            alias = _git_config_env_alias(token.removeprefix("--config-env="), assignments)
            if alias is not None:
                aliases[alias[0]] = alias[1]
            current += 1
            continue
        if _option_uses_value(token, _GIT_OPTIONS_WITH_VALUE, _GIT_OPTIONS_WITH_EQUALS):
            current += 2 if token in _GIT_OPTIONS_WITH_VALUE else 1
            continue
        if token.startswith("-"):
            current += 1
            continue
        return aliases
    return aliases


def _git_invokes(tokens: list[str], subcommand: str) -> bool:
    index = _first_command_index(tokens)
    if index is None or _command_name(tokens[index]) not in {"git", "git.exe"}:
        return False
    aliases = _git_aliases_before_subcommand(tokens, index)
    next_subcommand = _next_git_subcommand(tokens, index)
    return next_subcommand == subcommand or (
        next_subcommand in aliases
        and _git_alias_invokes(aliases[next_subcommand], subcommand, aliases, {next_subcommand})
    )


def _next_gh_subcommands(tokens: list[str], index: int, count: int) -> list[str]:
    current = index + 1
    subcommands: list[str] = []
    while current < len(tokens) and len(subcommands) < count:
        token = tokens[current]
        if token in _COMMAND_SEPARATORS:
            break
        if _option_uses_value(token, _GH_OPTIONS_WITH_VALUE, _GH_OPTIONS_WITH_EQUALS):
            current += 2 if token in _GH_OPTIONS_WITH_VALUE else 1
            continue
        if token.startswith("-"):
            current += 1
            continue
        subcommands.append(token)
        current += 1
    return subcommands


def _gh_invokes(tokens: list[str], subcommands: list[str]) -> bool:
    index = _first_command_index(tokens)
    return (
        index is not None
        and _command_name(tokens[index]) in {"gh", "gh.exe"}
        and _next_gh_subcommands(tokens, index, len(subcommands)) == subcommands
    )


def _embedded_shell_commands(tokens: list[str]) -> list[str]:
    embedded: list[str] = []
    first = _first_non_assignment_index(tokens)
    if first is not None and _command_name(tokens[first]) == "env":
        for option_index in range(first + 1, len(tokens)):
            option = tokens[option_index]
            if option in {"-S", "-s", "--split-string"} and option_index + 1 < len(tokens):
                embedded.append(tokens[option_index + 1])
                return embedded
            if option.startswith("-s") and option != "-s":
                embedded.append(option[2:])
                return embedded
            if option.startswith("--split-string="):
                embedded.append(option.removeprefix("--split-string="))
                return embedded
    index = _first_command_index(tokens)
    if index is None:
        return embedded
    command = _command_name(tokens[index])
    if command == "eval" and index + 1 < len(tokens):
        embedded.append(" ".join(tokens[index + 1 :]))
        return embedded
    if command in _SHELL_COMMANDS:
        for option_index in range(index + 1, len(tokens) - 1):
            option = tokens[option_index]
            if option == "-c" or (
                option.startswith("-") and not option.startswith("--") and "c" in option[1:]
            ):
                embedded.append(_strip_shell_dollar_quote(tokens[option_index + 1]))
                break
    if command in _CMD_COMMANDS:
        for option_index in range(index + 1, len(tokens) - 1):
            if tokens[option_index] in {"/c", "/k"}:
                embedded_command = " ".join(tokens[option_index + 1 :]).strip()
                if embedded_command:
                    embedded.append(embedded_command)
                break
    if command in _POWERSHELL_COMMANDS:
        for option_index in range(index + 1, len(tokens) - 1):
            if tokens[option_index] in {"-command", "-c"}:
                embedded.append(tokens[option_index + 1])
                break
    return embedded


def _git_alias_shell_commands(
    alias_name: str,
    aliases: dict[str, str | None],
    seen: set[str] | None = None,
) -> list[str]:
    if alias_name not in aliases:
        return []
    seen_aliases = set() if seen is None else set(seen)
    if alias_name in seen_aliases:
        return []
    seen_aliases.add(alias_name)
    alias_value = aliases.get(alias_name)
    if alias_value is None:
        return []
    shell_alias = alias_value.startswith("!")
    alias_command = alias_value.removeprefix("!").strip()
    if shell_alias:
        return [alias_command] if alias_command else []
    alias_tokens = _command_tokens(alias_command)
    if not alias_tokens:
        return []
    nested_alias = alias_tokens[0]
    if nested_alias in aliases:
        return _git_alias_shell_commands(nested_alias, aliases, seen_aliases)
    if _command_name(nested_alias) in {"git", "git.exe"}:
        return _git_shell_alias_commands(alias_tokens)
    return []


def _git_shell_alias_commands(tokens: list[str]) -> list[str]:
    index = _first_command_index(tokens)
    if index is None or _command_name(tokens[index]) not in {"git", "git.exe"}:
        return []
    aliases = _git_aliases_before_subcommand(tokens, index)
    next_subcommand = _next_git_subcommand(tokens, index)
    if next_subcommand is None:
        return []
    return _git_alias_shell_commands(next_subcommand, aliases)


def _command_substitution_spans(command: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    index = 0
    single_quoted = False
    double_quoted = False
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and not single_quoted:
            escaped = True
            index += 1
            continue
        if char == "'" and not double_quoted:
            single_quoted = not single_quoted
            index += 1
            continue
        if char == '"' and not single_quoted:
            double_quoted = not double_quoted
            index += 1
            continue
        if single_quoted:
            index += 1
            continue
        prefix = next((candidate for candidate in ("$(", "<(", ">(") if command.startswith(candidate, index)), None)
        if prefix is None:
            index += 1
            continue
        span_start = index
        depth = 1
        index += len(prefix)
        start = index
        inner_single_quoted = False
        inner_double_quoted = False
        inner_escaped = False
        while index < len(command) and depth > 0:
            char = command[index]
            if inner_escaped:
                inner_escaped = False
                index += 1
                continue
            if char == "\\" and not inner_single_quoted:
                inner_escaped = True
                index += 1
                continue
            if char == "'" and not inner_double_quoted:
                inner_single_quoted = not inner_single_quoted
                index += 1
                continue
            if char == '"' and not inner_single_quoted:
                inner_double_quoted = not inner_double_quoted
                index += 1
                continue
            if not inner_single_quoted and any(command.startswith(candidate, index) for candidate in ("$(", "<(", ">(")):
                depth += 1
                index += 2
                continue
            if char == ")" and not inner_single_quoted and not inner_double_quoted:
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if depth == 0:
            substitution = command[start:index].strip()
            if substitution:
                spans.append((span_start, index + 1, substitution))
        index += 1
    index = 0
    single_quoted = False
    double_quoted = False
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and not single_quoted:
            escaped = True
            index += 1
            continue
        if char == "'" and not double_quoted:
            single_quoted = not single_quoted
            index += 1
            continue
        if char == '"' and not single_quoted:
            double_quoted = not double_quoted
            index += 1
            continue
        if single_quoted or char != "`":
            index += 1
            continue
        span_start = index
        index += 1
        start = index
        escaped = False
        while index < len(command):
            char = command[index]
            if char == "\\" and not escaped:
                escaped = True
                index += 1
                continue
            if char == "`" and not escaped:
                substitution = command[start:index].strip()
                if substitution:
                    spans.append((span_start, index + 1, substitution))
                break
            escaped = False
            index += 1
        index += 1
    return spans


def _raw_command_substitutions(command: str) -> list[str]:
    return [substitution for _start, _end, substitution in _command_substitution_spans(command)]


def _simple_substitution_output(command: str) -> str | None:
    if _command_substitution_spans(command):
        return None
    segments = _command_segments(_command_tokens(command))
    if len(segments) != 1 or not segments[0]:
        return None
    tokens = segments[0]
    command_name = _command_name(tokens[0])
    if command_name in {"echo", "echo.exe"}:
        output = " ".join(tokens[1:]).strip()
        return output or None
    if command_name in {"printf", "printf.exe"} and len(tokens) > 1:
        if tokens[1] in {"%s", "%s\\n"} and len(tokens) > 2:
            output = " ".join(tokens[2:]).strip()
        else:
            output = tokens[1].strip()
        return output or None
    return None


def _command_substitution_output_variants(command: str) -> list[str]:
    variants: list[str] = []
    for start, end, substitution in _command_substitution_spans(command):
        output = _simple_substitution_output(substitution)
        if output is not None:
            variants.append(f"{command[:start]}{output}{command[end:]}")
    return variants


def _shell_parameter_variants(command: str) -> list[str]:
    expanded = _IFS_EXPANSION_PATTERN.sub(" ", command)
    return [expanded] if expanded != command else []


def _has_command_substitution(command: str) -> bool:
    return bool(_command_substitution_spans(command))


def _python_uses_code_option(tokens: list[str], index: int) -> bool:
    current = index + 1
    while current < len(tokens):
        token = tokens[current]
        token_lower = token.lower()
        if token_lower in _COMMAND_SEPARATORS:
            return False
        if token_lower == "-c" or (token_lower.startswith("-c") and token_lower != "-c"):
            return True
        if token_lower == "-m" or (token_lower.startswith("-m") and token_lower != "-m"):
            return False
        if token_lower == "--":
            return False
        if token == "-W" or token_lower == "--check-hash-based-pycs":
            current = min(current + 2, len(tokens))
            continue
        if (
            (token.startswith("-W") and token != "-W")
            or token == "-X"
            or (token.startswith("-X") and token != "-X")
            or token_lower.startswith("--check-hash-based-pycs=")
        ):
            current += 1 if token != "-X" else 2
            continue
        if not token_lower.startswith("-"):
            return False
        current += 1
    return False


def _has_opaque_interpreter_payload(tokens: list[str]) -> bool:
    index = _first_command_index(tokens)
    if index is None:
        return False
    command = _command_name(tokens[index])
    if command in _POWERSHELL_COMMANDS and any(
        token.lower() in {"-encodedcommand", "-enc", "-e", "/encodedcommand", "/enc", "/e"}
        for token in tokens[index + 1 :]
    ):
        return True
    if _python_command_name(command) and _python_uses_code_option(tokens, index):
        return True
    return False


def _has_unsupported_shell_expansion(command: str, depth: int = 0) -> bool:
    if depth > _COMMAND_EXPANSION_DEPTH_LIMIT:
        return True
    if "<<" in command:
        return True
    expansion_text = _strip_single_quoted_ranges(command)
    if _PARAMETER_EXPANSION_PATTERN.search(expansion_text):
        return True
    tokens = _command_tokens(command)
    for segment_tokens in _command_segments(_command_tokens(command, lower=False)):
        if _has_opaque_interpreter_payload(segment_tokens):
            return True
    for segment_tokens in _command_segments(tokens):
        for embedded_command in _embedded_shell_commands(segment_tokens):
            if _has_unsupported_shell_expansion(embedded_command, depth + 1):
                return True
    for _start, _end, substitution in _command_substitution_spans(command):
        if _simple_substitution_output(substitution) is None:
            return True
        if _has_unsupported_shell_expansion(substitution, depth + 1):
            return True
    return False


def _effective_command_segments(command: str, depth: int = 0) -> list[str]:
    if depth > _COMMAND_EXPANSION_DEPTH_LIMIT:
        normalized = _normalized_command(command)
        return [normalized] if normalized else []
    tokens = _command_tokens(command)
    segments: list[str] = []
    for substitution in _raw_command_substitutions(command):
        segments.extend(_effective_command_segments(substitution, depth + 1))
    for alias_command in _git_shell_alias_commands(tokens):
        segments.extend(_effective_command_segments(alias_command, depth + 1))
    for variant in _command_substitution_output_variants(command):
        if variant != command:
            segments.extend(_effective_command_segments(variant, depth + 1))
    for variant in _shell_parameter_variants(command):
        segments.extend(_effective_command_segments(variant, depth + 1))
    for segment_tokens in _command_segments(tokens):
        for alias_command in _git_shell_alias_commands(segment_tokens):
            segments.extend(_effective_command_segments(alias_command, depth + 1))
        embedded = _embedded_shell_commands(segment_tokens)
        if embedded:
            for embedded_command in embedded:
                segments.extend(_effective_command_segments(embedded_command, depth + 1))
            continue
        segment = " ".join(segment_tokens).strip()
        if segment:
            segments.append(segment)
    if not segments:
        normalized = _normalized_command(command)
        if normalized:
            segments.append(normalized)
    return segments


def _single_command_invokes(command: str, invocation: str) -> bool:
    tokens = _command_tokens(command)
    invocation_tokens = _normalized_command(invocation).split()
    if invocation_tokens[:1] == ["git"] and len(invocation_tokens) == 2:
        return _git_invokes(tokens, invocation_tokens[1])
    elif invocation_tokens[:1] == ["gh"] and len(invocation_tokens) > 1:
        return _gh_invokes(tokens, invocation_tokens[1:])
    return tokens[: len(invocation_tokens)] == invocation_tokens


def _command_invokes(command: str, invocation: str) -> bool:
    return _single_command_invokes(command, invocation) or any(
        _single_command_invokes(segment, invocation) for segment in _effective_command_segments(command)
    )


def _command_publishes_package(command: str) -> bool:
    segments = [command, *_effective_command_segments(command)]
    return any(_single_command_publishes_package(segment) for segment in segments)


def _single_command_publishes_package(command: str) -> bool:
    tokens = _command_tokens(command)
    index = _first_command_index(tokens)
    if index is None:
        return False
    command_name = _command_name(tokens[index])
    remaining = tokens[index + 1 :]
    if command_name in {"twine", "twine.exe"}:
        return "upload" in remaining
    if _python_command_name(command_name):
        for module_index in range(index + 1, len(tokens) - 1):
            if tokens[module_index] == "-m" and _command_name(tokens[module_index + 1]) in {"twine", "twine.exe"}:
                return "upload" in tokens[module_index + 2 :]
    if command_name in {
        "npm",
        "npm.cmd",
        "npm.exe",
        "pnpm",
        "pnpm.cmd",
        "pnpm.exe",
        "yarn",
        "yarn.cmd",
        "yarn.exe",
        "poetry",
        "poetry.exe",
        "uv",
        "uv.exe",
        "hatch",
        "hatch.exe",
        "flit",
        "flit.exe",
    }:
        return "publish" in remaining
    return False


def _command_merges(command: str) -> bool:
    return any(
        _command_invokes(command, invocation)
        for invocation in (
            "git merge",
            "gh pr merge",
        )
    )


def _command_creates_release(command: str) -> bool:
    return any(
        _command_invokes(command, invocation)
        for invocation in (
            "gh release create",
            "gh release upload",
            "git tag",
        )
    )


def _command_allowed_by_policy(command: str, allowed_commands: list[str]) -> bool:
    segments = _effective_command_segments(command)
    if not segments:
        return False
    return all(
        any(_single_command_invokes(segment, allowed) for allowed in allowed_commands)
        for segment in segments
    )


def _successful_result_has_evidence(commands_run: list[Any], validation_results: list[Any]) -> tuple[bool, str]:
    if not validation_results:
        return False, "executor result successful status requires validation evidence"
    if not commands_run:
        return False, "executor result successful status requires command evidence"
    return True, "ok"


def _parse_utc(value: Any) -> datetime | None:
    if not _non_empty_string(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _task_summary(task: dict[str, Any]) -> str:
    summary = task.get("summary")
    if _non_empty_string(summary):
        return summary
    return task.get("title", "")


def build_executor_task_packet(
    *,
    task: dict[str, Any],
    snapshot: dict[str, Any],
    repo_path: str | Path,
    allowed_paths: list[str],
    required_checks: list[str],
    max_minutes: int,
    max_tasks: int,
    stop_conditions: list[str],
    evidence_path: str | Path,
    allowed_commands: list[str] | None = None,
    denied_commands: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": EXECUTOR_TASK_SCHEMA_VERSION,
        "packet": "executor_task",
        "created_at": utc_now(),
        "operator_confirmation_required": True,
        "executor_started": False,
        "task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "summary": _task_summary(task),
            "task_type": task.get("task_type"),
            "bucket": task.get("bucket"),
            "source": task.get("source"),
            "drivers": list(task.get("drivers", [])),
            "evidence": deepcopy(task.get("evidence", {})),
        },
        "repo": {
            "name": snapshot.get("repo"),
            "path": str(Path(repo_path).expanduser().resolve()),
            "branch": snapshot.get("branch"),
            "head": snapshot.get("head"),
        },
        "snapshot": deepcopy(snapshot),
        "allowed_paths": list(allowed_paths),
        "required_checks": list(required_checks),
        "limits": {
            "max_minutes": max_minutes,
            "max_tasks": max_tasks,
        },
        "stop_conditions": list(stop_conditions),
        "command_policy": {
            "allowed_commands": list(allowed_commands or []),
            "denied_commands": list(denied_commands or []),
        },
        "expected_output": {
            "schema_version": EXECUTOR_RESULT_SCHEMA_VERSION,
            "evidence_path": str(evidence_path),
        },
        "permissions": {
            "may_commit": False,
            "may_push": False,
            "may_open_pr": False,
            "may_merge": False,
            "may_release": False,
            "may_publish_packages": False,
            "named_host_adapter": None,
        },
        "limitations": [
            "generic_contract_only",
            "executor_not_started_by_cadence",
            "named_host_adapter_not_selected",
        ],
    }


def validate_executor_task_packet(packet: Any) -> tuple[bool, str]:
    if not isinstance(packet, dict):
        return False, "executor task packet must be a JSON object"
    if packet.get("protocol_version") != PROTOCOL_VERSION:
        return False, "executor task protocol_version is invalid"
    if packet.get("schema_version") != EXECUTOR_TASK_SCHEMA_VERSION:
        return False, "executor task schema_version is invalid"
    if packet.get("packet") != "executor_task":
        return False, "executor task packet must be executor_task"
    if packet.get("operator_confirmation_required") is not True:
        return False, "executor task operator_confirmation_required must be true"
    if packet.get("executor_started") is not False:
        return False, "executor task executor_started must be false"
    task = packet.get("task")
    if not isinstance(task, dict):
        return False, "executor task task must be a JSON object"
    for field in ("id", "title", "summary", "task_type", "bucket"):
        if not _non_empty_string(task.get(field)):
            return False, f"executor task task.{field} is required"
    if task.get("task_type") not in TASK_TYPES:
        return False, "executor task task.task_type must be execution or discovery"
    if task.get("bucket") not in BUCKETS:
        return False, "executor task task.bucket must be one of XS, S, M, L, XL"
    repo = packet.get("repo")
    if not isinstance(repo, dict):
        return False, "executor task repo must be a JSON object"
    for field in ("name", "path", "branch", "head"):
        if not _non_empty_string(repo.get(field)):
            return False, f"executor task repo.{field} is required"
    snapshot = packet.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("id") is None:
        return False, "executor task snapshot with id is required"
    valid_snapshot, snapshot_reason = validate_repo_snapshot(snapshot)
    if not valid_snapshot:
        return False, f"executor task snapshot is invalid: {snapshot_reason}"
    if snapshot.get("repo") != repo["name"]:
        return False, "executor task snapshot repo must match repo.name"
    snapshot_cwd = _normalized_filesystem_path(snapshot.get("cwd"))
    repo_path = _normalized_filesystem_path(repo.get("path"))
    if snapshot_cwd is None or repo_path is None:
        return False, "executor task snapshot cwd and repo.path must be absolute local paths"
    if snapshot_cwd != repo_path:
        return False, "executor task snapshot cwd must match repo.path"
    if snapshot.get("branch") != repo["branch"]:
        return False, "executor task snapshot branch must match repo.branch"
    if snapshot.get("head") != repo["head"]:
        return False, "executor task snapshot head must match repo.head"
    if snapshot.get("dirty_worktree") is not False:
        return False, "executor task snapshot dirty_worktree must be false"
    if snapshot.get("repo_confidence") == "low":
        return False, "executor task snapshot repo_confidence must not be low"
    allowed_paths = packet.get("allowed_paths")
    if not _is_string_list(allowed_paths) or not allowed_paths:
        return False, "executor task allowed_paths must be a non-empty list of strings"
    for index, allowed_path in enumerate(allowed_paths):
        if _repo_relative_path(allowed_path) is None:
            return False, f"executor task allowed_paths[{index}] must be repo-relative"
    if not _is_string_list(packet.get("required_checks")):
        return False, "executor task required_checks must be a list of strings"
    limits = packet.get("limits")
    if not isinstance(limits, dict):
        return False, "executor task limits must be a JSON object"
    for field in ("max_minutes", "max_tasks"):
        value = limits.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return False, f"executor task limits.{field} must be a positive integer"
    stop_conditions = packet.get("stop_conditions")
    if not _is_string_list(stop_conditions) or not stop_conditions:
        return False, "executor task stop_conditions must be a non-empty list of strings"
    if not all(condition in stop_conditions for condition in DEFAULT_EXECUTOR_STOP_CONDITIONS):
        return False, "executor task stop_conditions must include built-in safety stops"
    command_policy = packet.get("command_policy", {})
    if not isinstance(command_policy, dict):
        return False, "executor task command_policy must be a JSON object"
    for field in ("allowed_commands", "denied_commands"):
        commands = command_policy.get(field, [])
        if not _is_string_list(commands):
            return False, f"executor task command_policy.{field} must be a list of strings"
    expected_output = packet.get("expected_output")
    if not isinstance(expected_output, dict):
        return False, "executor task expected_output must be a JSON object"
    if expected_output.get("schema_version") != EXECUTOR_RESULT_SCHEMA_VERSION:
        return False, "executor task expected_output.schema_version is invalid"
    evidence_path = expected_output.get("evidence_path")
    if not _non_empty_string(evidence_path):
        return False, "executor task expected_output.evidence_path is required"
    if _normalized_filesystem_path(evidence_path) is None:
        return False, "executor task expected_output.evidence_path must be absolute"
    permissions = packet.get("permissions")
    if not isinstance(permissions, dict):
        return False, "executor task permissions must be a JSON object"
    for field in ("may_commit", "may_push", "may_open_pr", "may_merge", "may_release", "may_publish_packages"):
        if permissions.get(field) is not False:
            return False, f"executor task permissions.{field} must be false"
    if permissions.get("named_host_adapter") is not None:
        return False, "executor task permissions.named_host_adapter must be null"
    return True, "ok"


def validate_executor_command(command: Any, task_packet: dict[str, Any]) -> tuple[bool, str]:
    valid_task, task_reason = validate_executor_task_packet(task_packet)
    if not valid_task:
        return False, f"invalid executor task packet: {task_reason}"
    if not _non_empty_string(command):
        return False, "executor command is required"
    command_text = str(command)
    command_policy = task_packet.get("command_policy", {})
    allowed_commands = command_policy.get("allowed_commands", []) if isinstance(command_policy, dict) else []
    denied_commands = command_policy.get("denied_commands", []) if isinstance(command_policy, dict) else []
    permissions = task_packet["permissions"]
    unsupported_shell_expansion = _has_unsupported_shell_expansion(command_text)
    if any(_command_invokes(command_text, denied) for denied in denied_commands):
        return False, "executor command is denied by command_policy"
    if denied_commands and not allowed_commands and (_has_command_substitution(command_text) or unsupported_shell_expansion):
        return False, "executor command is denied by command_policy"
    if allowed_commands and (
        unsupported_shell_expansion or not _command_allowed_by_policy(command_text, allowed_commands)
    ):
        return False, "executor command is outside allowed command_policy"
    if permissions.get("may_commit") is False and _command_invokes(command_text, "git commit"):
        return False, "executor command violates disabled commit permission"
    if permissions.get("may_push") is False and _command_invokes(command_text, "git push"):
        return False, "executor command violates disabled push permission"
    if permissions.get("may_open_pr") is False and _command_invokes(command_text, "gh pr create"):
        return False, "executor command violates disabled PR creation permission"
    if permissions.get("may_merge") is False and _command_merges(command_text):
        return False, "executor command violates disabled merge permission"
    if permissions.get("may_release") is False and _command_creates_release(command_text):
        return False, "executor command violates disabled release permission"
    if permissions.get("may_publish_packages") is False and _command_publishes_package(command_text):
        return False, "executor command violates disabled package publication permission"
    if unsupported_shell_expansion and any(
        permissions.get(field) is False
        for field in ("may_commit", "may_push", "may_open_pr", "may_merge", "may_release", "may_publish_packages")
    ):
        return False, "executor command contains unsupported shell expansion"
    return True, "ok"


def validate_executor_result_evidence(evidence: Any, task_packet: dict[str, Any]) -> tuple[bool, str]:
    valid_task, task_reason = validate_executor_task_packet(task_packet)
    if not valid_task:
        return False, f"invalid executor task packet: {task_reason}"
    if not isinstance(evidence, dict):
        return False, "executor result must be a JSON object"
    if evidence.get("schema_version") != EXECUTOR_RESULT_SCHEMA_VERSION:
        return False, "executor result schema_version is invalid"
    if evidence.get("packet") != "executor_result":
        return False, "executor result packet must be executor_result"
    if evidence.get("task_id") != task_packet["task"]["id"]:
        return False, "executor result task_id does not match task packet"
    if not _non_empty_string(evidence.get("executor_id")):
        return False, "executor result executor_id is required"
    started_at = _parse_utc(evidence.get("started_at"))
    if started_at is None:
        return False, "executor result started_at is invalid"
    ended_at = _parse_utc(evidence.get("ended_at"))
    if ended_at is None:
        return False, "executor result ended_at is invalid"
    if ended_at < started_at:
        return False, "executor result ended_at must be at or after started_at"
    if (ended_at - started_at).total_seconds() > task_packet["limits"]["max_minutes"] * 60:
        return False, "executor result elapsed time exceeds task limit"
    status = evidence.get("status")
    if status not in EXECUTOR_STATUSES:
        return False, "executor result status is invalid"
    permissions = task_packet["permissions"]
    files_changed = evidence.get("files_changed")
    if not isinstance(files_changed, list) or any(not _non_empty_string(path) for path in files_changed):
        return False, "executor result files_changed must be a list of strings"
    allowed_paths = task_packet["allowed_paths"]
    for index, path in enumerate(files_changed):
        if not _path_allowed(path, allowed_paths):
            return False, f"executor result files_changed[{index}] is outside allowed_paths"
    commands_run = evidence.get("commands_run")
    if not isinstance(commands_run, list):
        return False, "executor result commands_run must be a list"
    command_policy = task_packet.get("command_policy", {})
    allowed_commands = command_policy.get("allowed_commands", []) if isinstance(command_policy, dict) else []
    denied_commands = command_policy.get("denied_commands", []) if isinstance(command_policy, dict) else []
    command_exit_codes: dict[str, list[int]] = {}
    for index, command in enumerate(commands_run):
        if not isinstance(command, dict):
            return False, f"executor result commands_run[{index}] must be a JSON object"
        command_text = command.get("command")
        if not _non_empty_string(command_text):
            return False, f"executor result commands_run[{index}].command is required"
        exit_code = command.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            return False, f"executor result commands_run[{index}].exit_code must be an integer"
        unsupported_shell_expansion = _has_unsupported_shell_expansion(command_text)
        if any(_command_invokes(command_text, denied) for denied in denied_commands):
            return False, f"executor result commands_run[{index}] is denied by command_policy"
        if denied_commands and not allowed_commands and (_has_command_substitution(command_text) or unsupported_shell_expansion):
            return False, f"executor result commands_run[{index}] is denied by command_policy"
        if allowed_commands and (unsupported_shell_expansion or not _command_allowed_by_policy(command_text, allowed_commands)):
            return False, f"executor result commands_run[{index}] is outside allowed command_policy"
        if permissions.get("may_commit") is False and _command_invokes(command_text, "git commit"):
            return False, f"executor result commands_run[{index}] violates disabled commit permission"
        if permissions.get("may_push") is False and _command_invokes(command_text, "git push"):
            return False, f"executor result commands_run[{index}] violates disabled push permission"
        if permissions.get("may_open_pr") is False and _command_invokes(command_text, "gh pr create"):
            return False, f"executor result commands_run[{index}] violates disabled PR creation permission"
        if permissions.get("may_merge") is False and _command_merges(command_text):
            return False, f"executor result commands_run[{index}] violates disabled merge permission"
        if permissions.get("may_release") is False and _command_creates_release(command_text):
            return False, f"executor result commands_run[{index}] violates disabled release permission"
        if permissions.get("may_publish_packages") is False and _command_publishes_package(command_text):
            return False, f"executor result commands_run[{index}] violates disabled package publication permission"
        if unsupported_shell_expansion and any(
            permissions.get(field) is False
            for field in ("may_commit", "may_push", "may_open_pr", "may_merge", "may_release", "may_publish_packages")
        ):
            return False, f"executor result commands_run[{index}] contains unsupported shell expansion"
        command_exit_codes.setdefault(command_text, []).append(exit_code)
    validation_results = evidence.get("validation_results")
    if not isinstance(validation_results, list):
        return False, "executor result validation_results must be a list"
    validation_statuses_by_command: dict[str, list[str]] = {}
    for index, result in enumerate(validation_results):
        if not isinstance(result, dict):
            return False, f"executor result validation_results[{index}] must be a JSON object"
        if not _non_empty_string(result.get("name")):
            return False, f"executor result validation_results[{index}].name is required"
        if result.get("status") not in {"passed", "failed", "skipped"}:
            return False, f"executor result validation_results[{index}].status is invalid"
        if _non_empty_string(result.get("command")):
            validation_statuses_by_command.setdefault(result["command"], []).append(result["status"])
    if status == "succeeded":
        if not task_packet["required_checks"]:
            has_evidence, evidence_reason = _successful_result_has_evidence(commands_run, validation_results)
            if not has_evidence:
                return False, evidence_reason
        for required_check in task_packet["required_checks"]:
            exit_codes = command_exit_codes.get(required_check)
            if not exit_codes:
                return False, f"executor result missing required check command: {required_check}"
            if not any(exit_code == 0 for exit_code in exit_codes):
                return False, f"executor result required check command failed: {required_check}"
            validation_statuses = validation_statuses_by_command.get(required_check)
            if not validation_statuses:
                return False, f"executor result missing required check validation: {required_check}"
            if "passed" not in validation_statuses:
                return False, f"executor result required check validation did not pass: {required_check}"
        if any(result.get("status") != "passed" for result in validation_results):
            return False, "executor result successful status requires all validation_results to pass"
    if not _non_empty_string(evidence.get("summary")):
        return False, "executor result summary is required"
    if evidence.get("confidence") not in EXECUTOR_CONFIDENCE_VALUES:
        return False, "executor result confidence is invalid"
    if not _is_string_list(evidence.get("blockers")):
        return False, "executor result blockers must be a list of strings"
    if not isinstance(evidence.get("dirty_worktree"), bool):
        return False, "executor result dirty_worktree must be a boolean"
    if evidence.get("status") == "succeeded" and evidence.get("dirty_worktree") is not False:
        return False, "executor result dirty_worktree must be false when status is succeeded"
    resulting_head = evidence.get("resulting_head")
    if evidence.get("status") == "succeeded" and resulting_head is None:
        return False, "executor result resulting_head is required when status is succeeded"
    if resulting_head is not None and not _non_empty_string(resulting_head):
        return False, "executor result resulting_head must be a string or null"
    if permissions.get("may_commit") is False and resulting_head is not None and resulting_head != task_packet["repo"]["head"]:
        return False, "executor result resulting_head must match task repo head when commits are forbidden"
    return True, "ok"
