import subprocess
import sys
import tempfile
import tomllib
import unittest
import importlib
import importlib.util
import json
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE24_CHECKOUT_SHA = "de0fac2e4500dabe0009e67214ff5f5447ce83dd"
NODE24_SETUP_PYTHON_SHA = "a309ff8b426b58ec0e2a45f0f869d46889d02405"
NODE24_UPLOAD_ARTIFACT_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
NODE20_ACTION_SHAS = (
    "34e114876b0b11c390a56381ad16ebd13914f8d5",
    "a26af69be951a213d495a4c3e4e4022e16d87065",
    "ea165f8d65b6e75b540449e92b4886f43607fa02",
)
NODE24_ACTION_PINS = {
    "actions/checkout": NODE24_CHECKOUT_SHA,
    "actions/setup-python": NODE24_SETUP_PYTHON_SHA,
    "actions/upload-artifact": NODE24_UPLOAD_ARTIFACT_SHA,
}


def load_validate_protocol():
    spec = importlib.util.spec_from_file_location("validate_protocol", ROOT / "scripts" / "validate_protocol.py")
    validator = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(validator)
    return validator


def load_public_release_audit():
    spec = importlib.util.spec_from_file_location(
        "public_release_audit",
        ROOT / "scripts" / "public_release_audit.py",
    )
    audit = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(audit)
    return audit


def assert_node24_action_pins(test_case: unittest.TestCase, workflow_texts: dict[str, str]) -> None:
    combined = "\n".join(workflow_texts.values())

    for old_sha in NODE20_ACTION_SHAS:
        with test_case.subTest(old_sha=old_sha):
            test_case.assertNotIn(old_sha, combined)

    for name, text in workflow_texts.items():
        seen_actions: set[str] = set()
        for line_no, line in enumerate(text.splitlines(), start=1):
            line_without_comment = line.split("#", 1)[0].strip()
            match = re.match(r"^(?:-\s*)?uses:\s*(?P<value>\S+)\s*$", line_without_comment)
            if match is None:
                continue
            value = match.group("value").strip("'\"")
            action, separator, _ref = value.partition("@")
            if separator != "@" or action not in NODE24_ACTION_PINS:
                continue
            seen_actions.add(action)
            expected = f"{action}@{NODE24_ACTION_PINS[action]}"
            test_case.assertEqual(value, expected, f"{name}:{line_no} {action} must use the Node 24-compatible pin")

        for action, sha in NODE24_ACTION_PINS.items():
            with test_case.subTest(workflow=name, action=action):
                test_case.assertIn(action, seen_actions)
                test_case.assertIn(f"{action}@{sha}", text)


class CiChecksTests(unittest.TestCase):
    def tracked_public_claim_surfaces(self):
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

        public_suffixes = {".md", ".toml", ".yaml", ".yml"}
        return tuple(
            relative
            for relative in result.stdout.splitlines()
            if Path(relative).suffix.lower() in public_suffixes
        )

    def test_protocol_validator_accepts_current_repo(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_protocol.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_cli_smoke_lifecycle_runs(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ci_smoke.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_legacy_transmission_wrapper_still_runs(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "transmission.py"), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("Agentic Cadence", result.stdout)

    def test_legacy_transmission_wrapper_dispatches_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "transmission.py"), "--root", tmp, "status"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        output = json.loads(result.stdout)
        self.assertEqual(output["brake"]["status"], "DRIVE")
        self.assertEqual(output["counts"]["ready"], 0)

    def test_package_cli_module_runs_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "codex_cadence", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("Agentic Cadence", result.stdout)

    def test_script_wrappers_delegate_to_package_cli(self):
        cadence_text = (ROOT / "scripts" / "cadence.py").read_text(encoding="utf-8")
        transmission_text = (ROOT / "scripts" / "transmission.py").read_text(encoding="utf-8")

        self.assertIn("from codex_cadence.cli import main", cadence_text)
        self.assertIn("from transmission_control.cli import main", transmission_text)
        self.assertNotIn("from scripts.cadence import *", transmission_text)

    def test_transmission_control_cli_module_dispatches_help(self):
        code = "from transmission_control.cli import main; raise SystemExit(main(['--help']))"
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("Agentic Cadence", result.stdout)

    def test_pyproject_declares_public_package_metadata(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(pyproject["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertIn("setuptools>=77", pyproject["build-system"]["requires"])
        project = pyproject["project"]
        self.assertEqual(project["name"], "agentic-cadence")
        self.assertEqual(project["version"], "0.1.3")
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["readme"], "README.md")
        self.assertEqual(project["license"], "MIT")
        self.assertEqual(project["license-files"], ["LICENSE"])
        self.assertEqual(project["scripts"]["agentic-cadence"], "codex_cadence.cli:main")
        self.assertEqual(project["scripts"]["codex-cadence"], "codex_cadence.cli:main")
        self.assertEqual(project["scripts"]["codex-transmission"], "transmission_control.cli:main")
        self.assertEqual(project["urls"]["Homepage"], "https://github.com/Chef-Code/agentic-cadence")
        self.assertEqual(project["urls"]["Repository"], "https://github.com/Chef-Code/agentic-cadence")
        self.assertEqual(project["urls"]["Issues"], "https://github.com/Chef-Code/agentic-cadence/issues")
        self.assertNotIn("License :: OSI Approved :: MIT License", project["classifiers"])

    def test_public_identity_contract_keeps_agentic_primary_and_legacy_aliases(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = pyproject["project"]
        scripts = project["scripts"]

        self.assertEqual(project["name"], "agentic-cadence")
        self.assertEqual(
            scripts,
            {
                "agentic-cadence": "codex_cadence.cli:main",
                "codex-cadence": "codex_cadence.cli:main",
                "codex-transmission": "transmission_control.cli:main",
            },
        )
        keywords = {keyword.lower() for keyword in project["keywords"]}
        self.assertIn("agentic", keywords)
        self.assertIn("codex", keywords)
        self.assertNotIn("claude", keywords)
        self.assertNotIn("gemini", keywords)

    def test_pyproject_uses_explicit_package_discovery(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        package_find = pyproject["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(package_find["include"], ["codex_cadence*", "transmission_control*"])

    def test_license_file_declares_mit_for_chef_code(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 Chef-Code", license_text)

    def test_gitignore_covers_public_package_artifacts(self):
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")

        for token in (
            ".agentic-cadence/",
            ".agentic-cadence-demo/",
            ".codex-cadence-demo/",
            "examples/first-run/work/",
            "examples/generic-host-signal/work/",
            "examples/generic-shell-host-binding/work/",
            "dist/",
            "build/",
            "*.egg-info/",
        ):
            with self.subTest(token=token):
                self.assertIn(token, ignore_text)

    def test_readme_covers_public_quickstart(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        for token in (
            "Agentic Cadence",
            "Codex",
            "Claude",
            "Gemini",
            "python -m pip install .",
            "agentic-cadence --help",
            "agentic-cadence --root .agentic-cadence-demo status",
            "examples/first-run",
            "codex-transmission",
            "codex-cadence",
            "python -m codex_cadence --help",
            "agentic-cadence pr-body-preflight --body-file pr-body.md --pr-template-file .github/pull_request_template.md",
            "agentic-cadence release-dry-run --cwd . --version <version>",
            "MIT",
        ):
            with self.subTest(token=token):
                self.assertIn(token, readme)

    def test_adapter_direction_docs_define_future_agent_surface(self):
        adapters_path = ROOT / "docs" / "adapters.md"
        self.assertTrue(adapters_path.exists(), "missing adapter direction docs")

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        adapters = adapters_path.read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")

        for token in (
            "## Future Agent Adapters",
            "docs/adapters.md",
            "Claude",
            "Gemini",
        ):
            with self.subTest(location="README", token=token):
                self.assertIn(token, readme)

        for token in (
            "# Agent Adapter Direction",
            "agent-neutral",
            "host/session signal",
            "consume CLI JSON packets",
            "do not directly write Cadence runtime files",
            "call the CLI without private imports",
            "stop_current_session",
            "clean-square",
            "preserve the `prepare-handoff` packet relationship",
            "must not bypass Cadence governance",
            "No Claude or Gemini adapter is shipped in 0.1.x",
            "agentic-cadence status",
            "agentic-cadence prepare-handoff",
            "agentic-cadence approve-handoff",
            "agentic-cadence claim-handoff",
            "agentic-cadence complete-handoff",
            "agentic-cadence discover-candidates",
            "agentic-cadence pr-readiness",
            "agentic-cadence pr-body-preflight",
        ):
            with self.subTest(location="docs/adapters.md", token=token):
                self.assertIn(token, adapters)
        public_claim_surfaces = self.tracked_public_claim_surfaces()
        for expected in (
            "README.md",
            "SKILL.md",
            "docs/protocol.md",
            "docs/public-release.md",
            "docs/cadence/business-memory.md",
            "examples/adapter-template/README.md",
            "examples/adapter-template/host-binding-mapping.md",
            ".github/workflows/pr.yml",
        ):
            with self.subTest(surface=expected):
                self.assertIn(expected, public_claim_surfaces)

        unsupported_named_host_claim = (
            r"(?mi)^(?!\s*(?:[-*]\s*)?No\b).*(?:Claude|Gemini).*\badapters?"
            r"(?:\s+support)?\s+(?:is|are)\s+(?:shipped|supported)\b"
        )
        stale_clean_square_claim = "keep clean-square evidence tied to the repository snapshot that produced it"
        for relative in public_claim_surfaces:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(file=relative, claim="unsupported named host"):
                self.assertNotRegex(text, unsupported_named_host_claim)
            with self.subTest(file=relative, claim="stale clean-square evidence wording"):
                self.assertNotIn(stale_clean_square_claim, text)

    def test_adapter_claim_checklist_docs_define_preclaim_gate(self):
        checklist_path = ROOT / "docs" / "adapter-claim-checklist.md"
        self.assertTrue(checklist_path.exists(), "missing adapter claim checklist")

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        adapters = (ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        checklist = checklist_path.read_text(encoding="utf-8")

        for doc_name, text in (
            ("README", readme),
            ("adapter docs", adapters),
            ("roadmap", roadmap),
        ):
            with self.subTest(doc=doc_name):
                self.assertIn("docs/adapter-claim-checklist.md", text)

        for token in (
            "# Adapter Claim Checklist",
            "No Claude or Gemini adapter is shipped",
            "python examples/adapter-smoke/run.py --cadence-python python",
            "examples/adapter-template/host_signal_contract.py",
            "examples/generic-host-signal/run.py --cadence-python python",
            "examples/generic-host-signal/run.py --parity-contract --cadence-python python",
            "examples/generic-shell-host-binding/run.py --replay-contract --cadence-python python",
            "examples/external-host-binding-conformance/run.py --cadence-python python",
            "examples/adapter-contract-runner/run.py --cadence-python python",
            "--binding-command-template",
            "{host_event_file}",
            "{case_work_dir}",
            "{cadence_args}",
            "must forward `{cadence_args}`",
            "examples/adapter-template/host-binding-mapping.md",
            "preserve CLI JSON packets",
            "stop_current_session",
            "PLAY_ON",
            "do not claim named host support",
        ):
            with self.subTest(token=token):
                self.assertIn(token, checklist)

        normalized_checklist = re.sub(r"\s+", " ", checklist)
        binding_requirements = (
            r"--cadence-python\s+python",
            r"--binding-command-template",
            r"python\s+path/to/external-binding\.py",
            r"--host-event-file\s+[`'\"]?\{host_event_file\}[`'\"]?",
            r"--work-dir\s+[`'\"]?\{case_work_dir\}[`'\"]?",
            r"\{cadence_args\}",
        )
        for runner in (
            "examples/external-host-binding-conformance/run.py",
            "examples/adapter-contract-runner/run.py",
        ):
            with self.subTest(binding_template=runner):
                command = re.search(
                    rf"python\s+{re.escape(runner)}\b[^`]*--binding-command-template[^`]*",
                    normalized_checklist,
                )
                self.assertIsNotNone(command)
                for requirement in binding_requirements:
                    self.assertRegex(command.group(0), requirement)

    def test_roadmap_captures_current_edges_and_target_state(self):
        roadmap_path = ROOT / "docs" / "roadmap.md"
        self.assertTrue(roadmap_path.exists(), "missing technical roadmap")

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        roadmap = roadmap_path.read_text(encoding="utf-8")

        self.assertIn("[technical roadmap](docs/roadmap.md)", readme)

        for token in (
            "# Agentic Cadence Technical Roadmap",
            "## North Star",
            "## Current State",
            "## Known Edges",
            "## Target State",
            "## Roadmap",
            "## Non-Goals For 0.1.x",
            "## Open Questions",
            "released 0.1.3 baseline",
            "No Claude or Gemini adapter is shipped",
            "Runtime state is local filesystem state",
            "There is no automatic real-host context-pressure integration",
            "saved GitHub review-thread files",
            "Release verification is documented and repeatable",
            "copyable adapter template",
            "shared runtime backend",
            "No autonomous merge or release without explicit operator instruction",
        ):
            with self.subTest(token=token):
                self.assertIn(token, roadmap)
        self.assertNotIn("additional unreleased", roadmap)

    def test_readme_visual_identity_assets_exist(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        image_targets = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", readme)

        for asset in (
            "docs/assets/readme-badges.svg",
            "docs/assets/agentic-cadence-banner.svg",
            "docs/assets/handoff-flow.svg",
            "docs/assets/cadence-states.svg",
        ):
            with self.subTest(asset=asset):
                self.assertIn(asset, image_targets)

        for target in image_targets:
            with self.subTest(target=target):
                self.assertFalse(target.startswith(("http://", "https://")))
                self.assertTrue((ROOT / target).exists())

    def test_release_readiness_docs_cover_public_baseline(self):
        changelog_path = ROOT / "CHANGELOG.md"
        release_path = ROOT / "docs" / "release.md"
        self.assertTrue(changelog_path.exists())
        self.assertTrue(release_path.exists())

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        protocol = (ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        changelog = changelog_path.read_text(encoding="utf-8")
        release = release_path.read_text(encoding="utf-8")

        for token in (
            "## Current Status",
            "early public protocol and tooling release",
            "pip install .",
            "adapter smoke contract",
            "PyPI publication is not part of this baseline",
            "## Release Dry Run",
            "updated for the intended release version",
        ):
            with self.subTest(location="README", token=token):
                self.assertIn(token, readme)

        for text_name, text in (
            ("README", readme),
            ("SKILL", skill),
            ("protocol", protocol),
            ("release docs", release),
            ("roadmap", roadmap),
        ):
            with self.subTest(location=text_name, stale_release_dry_run_example=True):
                self.assertNotIn("release-dry-run --cwd . --version 0.1.1", text)

        for token in (
            "# Changelog",
            "## Unreleased",
            "## 0.1.3 - 2026-05-29",
            "Adapter contract runner now uses a short per-checkout default work directory",
            "## 0.1.2 - 2026-05-29",
            "Adapter contract and release-readiness baseline.",
            "generic-adapter-contract-evidence",
            "`--validate-evidence-file`",
            "No Claude or Gemini adapter is shipped",
            "## 0.1.1 - 2026-05-26",
            "Adapter smoke contract release",
            "Linux and Windows CI coverage",
            "Claude and Gemini host adapters",
            "## 0.1.0 - 2026-05-26",
            "Initial public release",
            "agent-neutral",
            "prepare-handoff",
            "pr-readiness",
        ):
            with self.subTest(location="CHANGELOG", token=token):
                self.assertIn(token, changelog)

        for token in (
            "# Release Checklist",
            "Manual GitHub Actions dry run",
            ".github/workflows/release-dry-run.yml",
            "python scripts/public_release_audit.py --history",
            "python scripts/cadence.py release-dry-run --cwd . --version <version>",
            "python scripts/validate_protocol.py",
            "python -m unittest discover -s tests -v",
            "python -m compileall scripts codex_cadence transmission_control tests",
            "python -m pip install --upgrade pip build",
            "python scripts/ci_smoke.py",
            "python scripts/verify_package.py",
            "git diff --check",
            "dedicated secret scanner",
            "not a substitute for generic secret scanning",
            "CHANGELOG.md",
            "GitHub release notes",
            "release_notes",
        ):
            with self.subTest(location="release docs", token=token):
                self.assertIn(token, release)

    def test_readme_five_minute_first_run_uses_only_clean_checkout_paths(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        heading = "## Five-Minute First Run"
        next_heading = "## Run The Example Workflow"
        self.assertIn(heading, readme)
        self.assertIn(next_heading, readme)

        section = readme[readme.index(heading) : readme.index(next_heading)]

        self.assertNotIn("examples/first-run/work", section)
        self.assertNotIn("discover-candidates", section)

    def test_protocol_handoff_docs_use_agent_neutral_context_wording(self):
        protocol = (ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")

        for stale_phrase in (
            "new Codex context",
            "fresh Codex session",
            "Codex work",
        ):
            with self.subTest(stale_phrase=stale_phrase):
                self.assertNotIn(stale_phrase, protocol)

    def test_first_run_example_files_exist(self):
        for relative in (
            "examples/first-run/handoff.md",
            "examples/first-run/repo/README.md",
            "examples/first-run/repo/docs/cadence/business-memory.md",
            "examples/first-run/run.sh",
            "examples/first-run/run.ps1",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).exists(), relative)

    def test_first_run_example_scripts_use_isolated_runtime(self):
        shell_text = (ROOT / "examples" / "first-run" / "run.sh").read_text(encoding="utf-8")
        powershell_text = (ROOT / "examples" / "first-run" / "run.ps1").read_text(encoding="utf-8")

        for text in (shell_text, powershell_text):
            self.assertIn("examples/first-run/work", text)
            self.assertIn("discover-candidates", text)
            self.assertIn("--root", text)
            self.assertIn("sources.business_memory", text)
            self.assertIn("CODEX_CADENCE_PYTHON", text)

    def test_first_run_example_executes_with_isolated_runtime(self):
        env = os.environ.copy()
        env["CODEX_CADENCE_PYTHON"] = sys.executable
        with tempfile.TemporaryDirectory() as cadence_root, tempfile.TemporaryDirectory() as legacy_root:
            env["CODEX_CADENCE_ROOT"] = cadence_root
            env["CODEX_TRANSMISSION_ROOT"] = legacy_root
            if sys.platform == "win32":
                command = [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "examples" / "first-run" / "run.ps1"),
                ]
            else:
                command = ["bash", str(ROOT / "examples" / "first-run" / "run.sh")]
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("Agentic Cadence first-run example completed.", result.stdout)

    def test_script_modules_keep_legacy_helper_imports(self):
        current = importlib.import_module("codex_cadence.cli")
        for module_name in ("scripts.cadence", "scripts.transmission", "transmission_control.cli"):
            with self.subTest(module_name=module_name):
                module = importlib.import_module(module_name)
                self.assertIs(module.main, current.main)
                self.assertIs(module.move_handoff, current.move_handoff)
                self.assertIs(module.atomic_write_json, current.atomic_write_json)

    def test_package_verification_script_exists(self):
        script = ROOT / "scripts" / "verify_package.py"

        self.assertTrue(script.exists(), "missing package verification script")
        text = script.read_text(encoding="utf-8")
        for token in (
            "python -m build",
            "pip install -e",
            "agentic-cadence",
            "codex-cadence",
            "codex-transmission",
            "python -m codex_cadence",
            "audit-replay",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_pr_workflow_runs_package_checks_on_linux_and_windows(self):
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")

        for token in (
            "package:",
            "ubuntu-latest",
            "windows-latest",
            "package_required",
            "Skip package checks for docs-only changes",
            "python -m pip install --upgrade pip build",
            "python scripts/verify_package.py",
            "python -m pip install .",
            "bash examples/first-run/run.sh",
            "pwsh examples/first-run/run.ps1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, workflow)
        self.assertLess(workflow.index("python -m pip install ."), workflow.index("bash examples/first-run/run.sh"))
        self.assertLess(workflow.index("python -m pip install ."), workflow.index("pwsh examples/first-run/run.ps1"))

    def test_discover_candidates_cli_honors_business_memory_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=tmp_root, text=True, capture_output=True, check=True)
            subprocess.run(["git", "add", "README.md"], cwd=tmp_root, text=True, capture_output=True, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test User",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    "Initial commit",
                ],
                cwd=tmp_root,
                text=True,
                capture_output=True,
                check=True,
            )
            memory = tmp_root / "docs" / "cadence" / "business-memory.md"
            memory.parent.mkdir(parents=True, exist_ok=True)
            memory.write_text(
                "\n".join(
                    [
                        "# Project Business Memory",
                        "",
                        "## Checkout planning is slow",
                        "",
                        "Kind: problem",
                        "Workflow: checkout planning",
                        "Time Saved: high",
                        "Risk: low",
                        "Pain: Engineers rebuild checkout context during planning.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "docs/cadence/business-memory.md"],
                cwd=tmp_root,
                text=True,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test User",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    "Add business memory",
                ],
                cwd=tmp_root,
                text=True,
                capture_output=True,
                check=True,
            )

            enabled_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "cadence.py"),
                    "discover-candidates",
                    "--cwd",
                    str(tmp_root),
                    "--intent",
                    "product_evolution",
                    "--max-business-memory-candidates",
                    "1",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            disabled_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "cadence.py"),
                    "discover-candidates",
                    "--cwd",
                    str(tmp_root),
                    "--intent",
                    "product_evolution",
                    "--max-business-memory-candidates",
                    "0",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(enabled_result.returncode, 0, enabled_result.stderr or enabled_result.stdout)
        enabled_output = json.loads(enabled_result.stdout)
        enabled_business_memory_candidates = [
            candidate for candidate in enabled_output["candidates"] if candidate["source"] == "business_memory"
        ]
        self.assertEqual(len(enabled_business_memory_candidates), 1)
        self.assertFalse(any("is untracked" in warning for warning in enabled_output["warnings"]))

        self.assertEqual(disabled_result.returncode, 0, disabled_result.stderr or disabled_result.stdout)
        output = json.loads(disabled_result.stdout)
        business_memory_candidates = [
            candidate for candidate in output["candidates"] if candidate["source"] == "business_memory"
        ]
        self.assertEqual(business_memory_candidates, [])
        self.assertEqual(output["sources"]["business_memory"], 0)

    def test_candidate_discovery_docs_cover_business_memory(self):
        validator = load_validate_protocol()
        skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        protocol_text = (ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
        business_memory_text = (ROOT / "docs" / "cadence" / "business-memory.md").read_text(encoding="utf-8")

        errors = []
        validator.validate_business_memory_contract(errors)
        self.assertEqual(errors, [])

        for file_name, text in (("SKILL.md", skill_text), ("docs/protocol.md", protocol_text)):
            taxonomy = validator.taxonomy_sentence_tokens(text)
            contract = validator.business_memory_contract_text(text)
            self.assertEqual(taxonomy, validator.EXPECTED_BUSINESS_MEMORY_TAXONOMY)
            for token in (
                "docs/cadence/business-memory.md",
                "source: business_memory",
                "maturity: discovery",
                "classification_confidence",
                "status",
                "active",
                "fulfilled",
                "superseded",
                "unclassified_signal",
                "classification: unknown",
                "repo_anchors: []",
                "evidence.path",
                "evidence.line",
                "evidence.heading",
                "discovery-only",
                "must not directly",
                "modify files",
                "commit",
                "push",
                "merge",
                "--max-business-memory-candidates",
            ):
                with self.subTest(file=file_name, token=token):
                    self.assertIn(token, contract)
        for token in ("growth", "efficiency", "learning"):
            with self.subTest(file="SKILL.md taxonomy", rejected_token=token):
                self.assertNotIn(token, validator.taxonomy_sentence_tokens(skill_text))
            with self.subTest(file="docs/protocol.md taxonomy", rejected_token=token):
                self.assertNotIn(token, validator.taxonomy_sentence_tokens(protocol_text))
        for file_name, text in (
            ("SKILL.md", skill_text),
            ("docs/protocol.md", protocol_text),
            ("docs/cadence/business-memory.md", business_memory_text),
        ):
            with self.subTest(file=file_name, status_taxonomy=True):
                self.assertEqual(validator.status_sentence_tokens(text), validator.EXPECTED_BUSINESS_MEMORY_STATUSES)

    def test_legacy_transmission_control_imports_alias_new_package(self):
        for module_name in ("store", "model", "repo_state", "epochs", "candidates"):
            with self.subTest(module_name=module_name):
                legacy = importlib.import_module(f"transmission_control.{module_name}")
                current = importlib.import_module(f"codex_cadence.{module_name}")
                self.assertIs(legacy, current)

    def test_pr_workflow_runs_required_checks(self):
        workflow = ROOT / ".github" / "workflows" / "pr.yml"
        self.assertTrue(workflow.exists(), "missing PR workflow")
        text = workflow.read_text(encoding="utf-8")

        for token in (
            "pull_request",
            "branches: [main]",
            "types: [opened, synchronize, reopened, ready_for_review]",
            "group: pr-checks-${{ github.event.pull_request.number }}",
            "cancel-in-progress: true",
            "Classify changed paths",
            "code_required",
            "Skip expensive code checks for docs-only changes",
            "git diff --check",
            "python -m compileall scripts codex_cadence transmission_control tests",
            "python -m unittest discover -s tests -v",
            "python scripts/validate_protocol.py",
            "python scripts/ci_smoke.py",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_release_dry_run_workflow_is_manual_read_only_and_artifacted(self):
        workflow = ROOT / ".github" / "workflows" / "release-dry-run.yml"
        self.assertTrue(workflow.exists(), "missing release dry-run workflow")
        text = workflow.read_text(encoding="utf-8")

        for token in (
            "workflow_dispatch:",
            "version:",
            "tag:",
            "target_ref:",
            "permissions:",
            "contents: read",
            "group: release-dry-run-${{ inputs.tag }}",
            "cancel-in-progress: true",
            "timeout-minutes: 10",
            "fetch-depth: 0",
            "persist-credentials: false",
            "python-version: \"3.12\"",
            "python scripts/cadence.py release-dry-run",
            "--version \"$RELEASE_VERSION\"",
            "--tag \"$RELEASE_TAG\"",
            "--target-ref \"$TARGET_REF\"",
            "python scripts/prepare_release_dry_run_artifacts.py",
            "python scripts/enforce_release_dry_run_result.py",
            "release-dry-run.json",
            "release-notes.md",
            "actions/upload-artifact@",
            "ready_to_release",
            "operator_confirmation_required",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)

        self.assertLess(text.index("Upload release dry-run artifacts"), text.index("Enforce release dry-run result"))
        self.assertIn('tee "$RUNNER_TEMP/release-dry-run.json"', text)
        self.assertIn('cp "$RUNNER_TEMP/release-dry-run.json" release-dry-run.json', text)
        self.assertNotIn("tee release-dry-run.json", text)
        self.assertIn("required: true", text[text.index("version:") : text.index("target_ref:")])
        self.assertIn("required: true", text[text.index("tag:") : text.index("target_ref:")])
        self.assertIn("required: false", text[text.index("target_ref:") : text.index("jobs:")])
        validator = load_validate_protocol()
        errors = []
        validator.validate_release_dry_run_workflow(errors)
        self.assertEqual(errors, [])

    def test_release_dry_run_artifact_scripts_prepare_outputs_before_enforcement(self):
        cases = (
            (
                "blocked",
                {
                    "ready_to_release": False,
                    "operator_confirmation_required": True,
                    "recommended_next_action": "address_blockers",
                    "release_notes": "## 0.2.0 - 2026-05-27\n\nBlocked release notes.\n",
                    "warnings": [{"code": "warn:one,two", "message": "line%value\nnext\r"}],
                    "blockers": [{"code": "blocker:one,two", "message": "blocked%value\nnext\r"}],
                },
                1,
                "release_blocked",
            ),
            (
                "ready",
                {
                    "ready_to_release": True,
                    "operator_confirmation_required": True,
                    "recommended_next_action": "create_tag_after_operator_confirmation",
                    "release_notes": "## 0.2.0 - 2026-05-27\n\nReady release notes.\n",
                    "warnings": [],
                    "blockers": [],
                },
                0,
                "No tags, GitHub releases, or package publications are created",
            ),
        )

        for name, packet, expected_returncode, expected_output in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                (tmp_root / "release-dry-run.json").write_text(json.dumps(packet), encoding="utf-8")
                output_path = tmp_root / "github-output.txt"
                env = os.environ.copy()
                env["GITHUB_OUTPUT"] = str(output_path)

                prepare = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "prepare_release_dry_run_artifacts.py")],
                    cwd=tmp_root,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                outputs = dict(
                    line.split("=", 1)
                    for line in output_path.read_text(encoding="utf-8").splitlines()
                    if "=" in line
                )
                enforce_env = env.copy()
                enforce_env["READY_TO_RELEASE"] = outputs["ready_to_release"]
                enforce_env["OPERATOR_CONFIRMATION_REQUIRED"] = outputs["operator_confirmation_required"]
                enforce = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "enforce_release_dry_run_result.py")],
                    cwd=tmp_root,
                    env=enforce_env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                notes = (tmp_root / "release-notes.md").read_text(encoding="utf-8")

            self.assertEqual(prepare.returncode, 0, prepare.stderr or prepare.stdout)
            self.assertEqual(notes, packet["release_notes"])
            self.assertEqual(outputs["ready_to_release"], str(packet["ready_to_release"]).lower())
            self.assertEqual(outputs["operator_confirmation_required"], "true")
            self.assertEqual(enforce.returncode, expected_returncode, enforce.stderr or enforce.stdout)
            self.assertIn(expected_output, enforce.stdout)
            if name == "blocked":
                self.assertIn("::warning title=warn%3Aone%2Ctwo::line%25value%0Anext%0D", prepare.stdout)
                self.assertIn("::error title=blocker%3Aone%2Ctwo::blocked%25value%0Anext%0D", prepare.stdout)

    def test_github_actions_are_pinned_to_full_commit_shas(self):
        workflow_dir = ROOT / ".github" / "workflows"
        self.assertTrue(workflow_dir.exists(), "missing workflow directory")

        for workflow in sorted(workflow_dir.glob("*.yml")):
            with self.subTest(workflow=workflow.name):
                for line_no, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), start=1):
                    match = re.match(r"^(?:-\s*)?uses:\s*(?P<value>\S+)\s*$", line.strip())
                    if match is None:
                        continue
                    value = match.group("value").strip("'\"")
                    if value.startswith("./"):
                        continue
                    self.assertRegex(
                        value,
                        r"@[0-9a-f]{40}$",
                        f"{workflow.relative_to(ROOT)}:{line_no} must pin actions to a full commit SHA",
                    )
                    self.assertNotRegex(value, r"@v\d+$")

    def test_pr_and_release_workflows_use_node24_compatible_action_pins(self):
        workflow_texts = {
            "pr": (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8"),
            "release": (ROOT / ".github" / "workflows" / "release-dry-run.yml").read_text(encoding="utf-8"),
        }
        assert_node24_action_pins(self, workflow_texts)

    def test_node24_action_pin_guard_rejects_drift_in_each_matching_action_use(self):
        expected = f"actions/checkout@{NODE24_CHECKOUT_SHA}"
        workflow_text = "\n".join(
            (
                f"      - uses: {expected}",
                f"      - uses: actions/setup-python@{NODE24_SETUP_PYTHON_SHA}",
                f"      - uses: actions/upload-artifact@{NODE24_UPLOAD_ARTIFACT_SHA}",
                f"      - uses: {expected}",
            )
        )
        occurrences = [match.start() for match in re.finditer(re.escape(expected), workflow_text)]
        self.assertGreaterEqual(len(occurrences), 2, f"expected checkout pin appears at least twice: {expected}")
        second = occurrences[1]
        drifted_workflow_text = (
            workflow_text[:second]
            + "actions/checkout@1111111111111111111111111111111111111111"
            + workflow_text[second + len(expected) :]
        )

        with self.assertRaises(AssertionError):
            assert_node24_action_pins(self, {"synthetic": drifted_workflow_text})

    def test_node24_action_pin_guard_rejects_drift_with_inline_comment(self):
        expected = f"actions/checkout@{NODE24_CHECKOUT_SHA}"
        drifted_workflow_text = "\n".join(
            (
                "      - uses: actions/checkout@1111111111111111111111111111111111111111 # drifted checkout",
                f"      - uses: actions/setup-python@{NODE24_SETUP_PYTHON_SHA}",
                f"      - uses: actions/upload-artifact@{NODE24_UPLOAD_ARTIFACT_SHA}",
                f"      - uses: {expected}",
            )
        )

        with self.assertRaises(AssertionError):
            assert_node24_action_pins(self, {"synthetic": drifted_workflow_text})

    def test_codeowners_covers_public_release_guardrails(self):
        codeowners = ROOT / ".github" / "CODEOWNERS"
        self.assertTrue(codeowners.exists(), "missing CODEOWNERS")
        text = codeowners.read_text(encoding="utf-8")

        for token in (
            ".github/workflows/**",
            ".github/CODEOWNERS",
            "scripts/codex_review_preflight.py",
            "scripts/validate_protocol.py",
            "scripts/public_release_audit.py",
            "codex_cadence/cli.py",
            "codex_cadence/release.py",
            "tests/test_ci_checks.py",
            "tests/test_release_dry_run.py",
            "@Chef-Code",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_public_release_audit_current_tree_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "public_release_audit.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("public release audit passed", result.stdout)

    def test_public_release_audit_rejects_unpinned_shorthand_uses(self):
        audit = load_public_release_audit()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            workflow = tmp_root / ".github" / "workflows" / "shorthand.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "\n".join(
                    [
                        "name: shorthand",
                        "jobs:",
                        "  checks:",
                        "    steps:",
                        "      - uses: actions/checkout@v5",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            original_root = audit.ROOT
            audit.ROOT = tmp_root
            try:
                findings = audit.check_workflow_pins()
            finally:
                audit.ROOT = original_root

        self.assertEqual(
            findings,
            [".github/workflows/shorthand.yml:5: action is not pinned to a full commit SHA: actions/checkout@v5"],
        )

    def test_public_release_docs_describe_clean_history_gate(self):
        docs = (ROOT / "docs" / "public-release.md").read_text(encoding="utf-8")
        before_pr_section = docs[docs.index("## Current Private Repository") : docs.index("## Clean Public History")]

        for token in (
            "python scripts\\public_release_audit.py",
            "python scripts\\public_release_audit.py --history",
            "python scripts\\cadence.py release-dry-run --cwd . --version <version>",
            "after the release PR is merged to a clean checked-out `main`",
            "origin/main",
            "target_branch_mismatch",
            "target_not_at_origin_branch",
            ".github/workflows/release-dry-run.yml",
            "Deleted files remain available in Git history",
            "clean public mirror",
            "Require CODEOWNERS review",
            "Block force pushes",
        ):
            with self.subTest(token=token):
                self.assertIn(token, docs)
        self.assertNotIn("release-dry-run", before_pr_section)

    def test_codex_review_workflow_runs_with_review_only_safeguards(self):
        workflow = ROOT / ".github" / "workflows" / "codex-review.yml"
        prompt = ROOT / ".github" / "codex" / "prompts" / "review.md"
        self.assertTrue(workflow.exists(), "missing Codex review workflow")
        self.assertFalse(prompt.exists(), "Codex review prompt must stay inline in the trusted workflow")

        workflow_text = workflow.read_text(encoding="utf-8")
        concurrency_group = next(
            line.strip()
            for line in workflow_text.splitlines()
            if line.strip().startswith("group: codex-review-")
        )
        self.assertEqual(
            concurrency_group,
            "group: codex-review-${{ github.event.pull_request.number }}",
        )

        for token in (
            "pull_request_target",
            "branches: [main]",
            "types: [labeled, synchronize]",
            "labeled",
            "github.event.action == 'labeled'",
            "github.event.action == 'synchronize'",
            "github.event.label.name == 'codex-review-elect'",
            "github.event.label.name == 'elect-codex-review'",
            "github.event.label.name == 'codex-review-force'",
            "github.event.label.name == 'force-codex-review'",
            "github.event.pull_request.draft == false",
            "github.event.pull_request.head.repo.full_name == github.repository",
            "github.event.pull_request.head.repo.full_name != github.repository",
            "Codex Review skipped for fork PRs",
            "untrusted PR code",
            "Check live PR state",
            "Re-check live PR state before paid review",
            "steps.live_pr.outputs.can_review == 'true'",
            "steps.review_pr.outputs.can_review == 'true'",
            "pull.draft === false",
            "pull.head.sha === context.payload.pull_request.head.sha",
            "pr_draft",
            "stale_head",
            "contents: read",
            "pull-requests: write",
            "ref: ${{ github.event.pull_request.head.sha }}",
            "BASE_REF: ${{ github.event.pull_request.base.ref }}",
            "HEAD_SHA: ${{ github.event.pull_request.head.sha }}",
            "PR_NUMBER: ${{ github.event.pull_request.number }}",
            '"+refs/heads/${BASE_REF}:refs/remotes/origin/${BASE_REF}"',
            "openai/codex-action@a26d2d4d8b78a694338b8e3715c3630254340b2c",
            "openai-api-key: ${{ secrets.OPENAI_API_KEY }}",
            "prompt: |",
            "safety-strategy: drop-sudo",
            "sandbox: read-only",
            "final-message",
            "actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b",
            "concurrency:",
            "cancel-in-progress: >-",
            "codex_review_preflight.py",
            "needs: preflight",
            "needs.preflight.outputs.should_run == 'true'",
            "timeout-minutes: 20",
            "ref: ${{ github.event.pull_request.base.sha }}",
            "path: trusted-preflight",
            "path: pr-head",
            "working-directory: pr-head",
            "python ../trusted-preflight/scripts/codex_review_preflight.py",
            '--head-ref "${HEAD_SHA}"',
            "issues: read",
            "pull-requests: read",
            "codex-review:v1 head=",
            "CODEX_REVIEW_DEDUPE_KEY",
            "PR_LABELS_JSON",
            "codex-review-elect",
            "toJson(github.event.pull_request.labels.*.name)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, workflow_text)

        for token in ("opened", "ready_for_review", "unlabeled"):
            with self.subTest(removed_trigger=token):
                self.assertNotIn(token, workflow_text.split("permissions:", 1)[0])
        preflight_condition = workflow_text[
            workflow_text.index("  preflight:") : workflow_text.index("    runs-on: ubuntu-latest")
        ]
        self.assertIn("github.event.action == 'labeled'", preflight_condition)
        self.assertNotIn("github.event.action == 'synchronize'", preflight_condition)

        self.assertNotIn("prompt-file:", workflow_text)
        self.assertNotIn("[skip codex]", workflow_text)
        self.assertNotIn("[force codex]", workflow_text)
        self.assertNotIn("python scripts/codex_review_preflight.py", workflow_text)
        self.assertNotIn("refs/pull/${{ github.event.pull_request.number }}/merge", workflow_text)
        self.assertNotIn("refs/remotes/pull/${PR_NUMBER}/head", workflow_text)
        self.assertLess(
            workflow_text.index("python ../trusted-preflight/scripts/codex_review_preflight.py"),
            workflow_text.index("openai-api-key: ${{ secrets.OPENAI_API_KEY }}"),
        )
        self.assertLess(
            workflow_text.index("ref: ${{ github.event.pull_request.base.sha }}"),
            workflow_text.index("python ../trusted-preflight/scripts/codex_review_preflight.py"),
        )
        self.assertEqual(workflow_text.count("issues: write"), 1)
        self.assertEqual(workflow_text.count("pull-requests: write"), 1)
        self.assertRegex(
            workflow_text,
            r"(?s)  codex:\n.*?    permissions:\n      contents: read\n      pull-requests: read",
        )
        self.assertRegex(
            workflow_text,
            r"(?s)  codex:\n.*?    timeout-minutes: 20",
        )
        self.assertRegex(
            workflow_text,
            r"(?s)  codex:\n    if: .*needs\.preflight\.outputs\.should_run == 'true'.*?    needs: preflight",
        )
        self.assertRegex(
            workflow_text,
            r"(?s)- name: Re-check live PR state before paid review.*?- name: Run Codex review\n        if: steps\.review_pr\.outputs\.can_review == 'true'",
        )
        self.assertRegex(
            workflow_text,
            r"(?s)  post_feedback:\n.*?    permissions:\n      issues: write\n      pull-requests: write",
        )

        for token in (
            "Do not modify files",
            "protocol drift",
            "race conditions",
            "missing tests",
            "actionable findings",
        ):
            with self.subTest(token=token):
                self.assertIn(token, workflow_text)

    def test_public_tree_excludes_private_context_docs(self):
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        tracked_files = result.stdout.splitlines()
        self.assertFalse(
            any(path.startswith("docs/superpowers/") and (ROOT / path).exists() for path in tracked_files),
            "internal superpowers plans/specs should not be tracked in the public tree",
        )

        disallowed = (
            "Chef-Code/" + "codex-" + "transmission-control",
            "W" + "DA",
            "wda" + "-systems",
            "wda" + "mo",
            "C:" + "\\Users\\",
        )
        for relative in tracked_files:
            path = ROOT / relative
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for token in disallowed:
                with self.subTest(file=relative, token=token):
                    self.assertNotIn(token, text)

    def test_protocol_validator_rejects_legacy_codex_review_refs(self):
        validator = load_validate_protocol()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            for relative in validator.REQUIRED_TOKENS:
                source = ROOT / relative
                target = tmp_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            workflow = tmp_root / ".github" / "workflows" / "codex-review.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8")
                + "\n# refs/pull/${{ github.event.pull_request.number }}/merge\n",
                encoding="utf-8",
            )

            original_root = validator.ROOT
            validator.ROOT = tmp_root
            try:
                errors = []
                validator.validate_tokens(errors)
            finally:
                validator.ROOT = original_root

        self.assertTrue(
            any("forbidden token" in error for error in errors),
            f"expected forbidden token error, got {errors}",
        )

    def test_protocol_validator_rejects_codex_review_trigger_drift(self):
        validator = load_validate_protocol()
        source = ROOT / ".github" / "workflows" / "codex-review.yml"

        cases = (
            (
                "duplicate_types",
                lambda text: text.replace(
                    "    types: [labeled, synchronize]",
                    "    types: [labeled, synchronize]\n    types: [opened, synchronize, ready_for_review]",
                ),
                "pull_request_target types must be exactly [labeled, synchronize]",
            ),
            (
                "legacy_opened_trigger",
                lambda text: text.replace(
                    "    types: [labeled, synchronize]",
                    "    types: [labeled, synchronize, opened]",
                ),
                "pull_request_target types must be exactly [labeled, synchronize]",
            ),
            (
                "missing_cancel_only_synchronize",
                lambda text: text.replace(
                    "    types: [labeled, synchronize]",
                    "    types: [labeled]",
                ),
                "pull_request_target types must be exactly [labeled, synchronize]",
            ),
            (
                "bare_cancellation",
                lambda text: text.replace(
                    "  cancel-in-progress: >-\n"
                    "    ${{\n"
                    "      github.event.action == 'synchronize' ||\n"
                    "      github.event.label.name == 'codex-review-elect' ||\n"
                    "      github.event.label.name == 'elect-codex-review' ||\n"
                    "      github.event.label.name == 'codex-review-force' ||\n"
                    "      github.event.label.name == 'force-codex-review'\n"
                    "    }}",
                    "  cancel-in-progress: true",
                ),
                "concurrency cancellation must be conditional",
            ),
            (
                "preflight_on_synchronize",
                lambda text: text.replace(
                    "      github.event.action == 'labeled' &&",
                    "      github.event.action == 'synchronize' &&",
                    1,
                ),
                "synchronize must be cancel-only",
            ),
        )

        for name, mutate, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                workflow = tmp_root / ".github" / "workflows" / "codex-review.yml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text(mutate(source.read_text(encoding="utf-8")), encoding="utf-8")

                original_root = validator.ROOT
                validator.ROOT = tmp_root
                try:
                    errors = []
                    validator.validate_codex_review_workflow(errors)
                finally:
                    validator.ROOT = original_root

            self.assertTrue(any(expected in error for error in errors), f"expected {expected!r}, got {errors}")

    def test_protocol_validator_rejects_stale_handoff_pr_state(self):
        validator = load_validate_protocol()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            for relative in validator.REQUIRED_TOKENS:
                source = ROOT / relative
                target = tmp_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            handoff = tmp_root / "docs" / "session-handoff.md"
            handoff.write_text(
                handoff.read_text(encoding="utf-8")
                + "\n- Working branch: `codex/policy-stop-controls`\n"
                + "- Current branch intent: Draft PR #58 is open for this branch.\n"
                + "Continue PR #58 review, monitor CI and bot feedback, address any remaining findings, and merge only after the operator is satisfied.\n"
                + "\n- Working branch: `codex/controlled-executor-fixture`\n"
                + "- This branch starts only a controlled fake executor fixture.\n",
                encoding="utf-8",
            )

            original_root = validator.ROOT
            validator.ROOT = tmp_root
            try:
                errors = []
                validator.validate_tokens(errors)
            finally:
                validator.ROOT = original_root

        expected_errors = (
            "docs/session-handoff.md must not contain forbidden token: codex/policy-stop-controls",
            "docs/session-handoff.md must not contain forbidden token: Draft PR #58",
            "docs/session-handoff.md must not contain forbidden token: Continue PR #58",
            "docs/session-handoff.md must not contain forbidden token: codex/controlled-executor-fixture",
            "docs/session-handoff.md must not contain forbidden token: This branch starts only a controlled fake executor fixture",
        )
        for expected_error in expected_errors:
            with self.subTest(expected_error=expected_error):
                self.assertTrue(
                    any(expected_error in error for error in errors),
                    f"expected stale handoff error {expected_error!r}, got {errors}",
                )

    def test_protocol_validator_rejects_git_pr_plan_guard_drift(self):
        validator = load_validate_protocol()

        cases = (
            (
                "dry_run_disabled",
                lambda text: text.replace("dry_run: true", "dry_run: false"),
                "dry_run: true",
            ),
            (
                "side_effects_added",
                lambda text: text.replace("side_effects: []", "side_effects: ['git']"),
                "side_effects: []",
            ),
            (
                "materialized_evidence_omitted",
                lambda text: text.replace("materialized_change_evidence", "change_evidence"),
                "materialized_change_evidence",
            ),
            (
                "github_calls_allowed",
                lambda text: text.replace("No GitHub API calls.", "GitHub API calls allowed after approval."),
                "No GitHub API calls",
            ),
        )

        for name, mutate, expected_token in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                for relative in validator.REQUIRED_TOKENS:
                    source = ROOT / relative
                    target = tmp_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

                design = tmp_root / "docs" / "designs" / "2026-06-01-git-pr-dry-run-plan-design.md"
                design.write_text(mutate(design.read_text(encoding="utf-8")), encoding="utf-8")

                original_root = validator.ROOT
                validator.ROOT = tmp_root
                try:
                    errors = []
                    validator.validate_tokens(errors)
                finally:
                    validator.ROOT = original_root

            expected_error = (
                "docs/designs/2026-06-01-git-pr-dry-run-plan-design.md "
                f"missing required token: {expected_token}"
            )
            self.assertTrue(
                any(expected_error in error for error in errors),
                f"expected git-pr-plan guard drift error {expected_error!r}, got {errors}",
            )

    def test_protocol_validator_rejects_release_workflow_guard_drift(self):
        validator = load_validate_protocol()
        source = ROOT / ".github" / "workflows" / "release-dry-run.yml"

        cases = (
            (
                "tag_not_required",
                lambda text: text.replace(
                    "      tag:\n        description: Release tag to verify, usually v<version>.\n        required: true",
                    "      tag:\n        description: Release tag to verify, usually v<version>.\n        required: false",
                ),
                "tag input must be required",
            ),
            (
                "scheduled",
                lambda text: text.replace(
                    "  workflow_dispatch:\n",
                    "  workflow_dispatch:\n  schedule:\n    - cron: '0 0 * * *'\n",
                ),
                "must declare only workflow_dispatch",
            ),
            (
                "repository_dispatch",
                lambda text: text.replace("  workflow_dispatch:\n", "  workflow_dispatch:\n  repository_dispatch:\n"),
                "must declare only workflow_dispatch",
            ),
            (
                "job_write_permission",
                lambda text: text.replace(
                    "    steps:\n",
                    "    permissions:\n      packages: write\n    steps:\n",
                ),
                "must not define job-level permissions",
            ),
            (
                "inline_job_write_permission",
                lambda text: text.replace(
                    "    steps:\n",
                    "    permissions: { contents: write }\n    steps:\n",
                ),
                "must not define job-level permissions",
            ),
            (
                "workflow_write_permission",
                lambda text: text.replace("  contents: read\n", "  contents: read\n  id-token: write\n"),
                "workflow permissions must be exactly contents: read",
            ),
            (
                "missing_ready_failure",
                lambda text: text.replace(
                    "          READY_TO_RELEASE: ${{ steps.inspect.outputs.ready_to_release }}\n",
                    "",
                ),
                "must fail when ready_to_release is false",
            ),
            (
                "missing_confirmation_failure",
                lambda text: text.replace(
                    "          OPERATOR_CONFIRMATION_REQUIRED: ${{ steps.inspect.outputs.operator_confirmation_required }}\n",
                    "",
                ),
                "must fail when operator_confirmation_required is false",
            ),
            (
                "artifacts_after_failure",
                lambda text: text.replace(
                    "      - name: Upload release dry-run artifacts",
                    "      - name: Enforce release dry-run result\n"
                    "        shell: bash\n"
                    "        run: exit 1\n\n"
                    "      - name: Upload release dry-run artifacts",
                    1,
                ),
                "must upload artifacts before enforcing failure",
            ),
            (
                "workspace_packet_tee",
                lambda text: text.replace('tee "$RUNNER_TEMP/release-dry-run.json"', "tee release-dry-run.json"),
                "must write dry-run output outside the checkout before artifact copy",
            ),
            (
                "git_push",
                lambda text: text.replace(
                    "      - name: Enforce release dry-run result",
                    "      - name: Illegal mutation\n"
                    "        shell: bash\n"
                    "        run: git push origin main\n\n"
                    "      - name: Enforce release dry-run result",
                    1,
                ),
                "forbidden release command: git push",
            ),
            (
                "piped_git_push",
                lambda text: text.replace(
                    "      - name: Enforce release dry-run result",
                    "      - name: Illegal mutation\n"
                    "        shell: bash\n"
                    "        run: true | git push origin main\n\n"
                    "      - name: Enforce release dry-run result",
                    1,
                ),
                "forbidden release command: git push",
            ),
            (
                "git_merge",
                lambda text: text.replace(
                    "      - name: Enforce release dry-run result",
                    "      - name: Illegal mutation\n"
                    "        shell: bash\n"
                    "        run: git merge release-candidate\n\n"
                    "      - name: Enforce release dry-run result",
                    1,
                ),
                "forbidden release command: git merge",
            ),
            (
                "github_release_action",
                lambda text: text.replace(
                    "      - name: Enforce release dry-run result",
                    "      - name: Illegal release action\n"
                    "        uses: softprops/action-gh-release@1111111111111111111111111111111111111111\n\n"
                    "      - name: Enforce release dry-run result",
                    1,
                ),
                "release or publishing action: softprops/action-gh-release",
            ),
        )

        for name, mutate, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                workflow = tmp_root / ".github" / "workflows" / "release-dry-run.yml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text(mutate(source.read_text(encoding="utf-8")), encoding="utf-8")

                original_root = validator.ROOT
                validator.ROOT = tmp_root
                try:
                    errors = []
                    validator.validate_release_dry_run_workflow(errors)
                finally:
                    validator.ROOT = original_root

            self.assertTrue(any(expected in error for error in errors), f"expected {expected!r}, got {errors}")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            workflow = tmp_root / ".github" / "workflows" / "release-dry-run.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                source.read_text(encoding="utf-8")
                + "\n# Operational notes may mention git push or push: without adding a trigger.\n",
                encoding="utf-8",
            )

            original_root = validator.ROOT
            validator.ROOT = tmp_root
            try:
                errors = []
                validator.validate_release_dry_run_workflow(errors)
            finally:
                validator.ROOT = original_root

        self.assertEqual(errors, [])

    def test_protocol_validator_rejects_business_memory_status_drift(self):
        validator = load_validate_protocol()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            for relative in (
                "SKILL.md",
                "docs/protocol.md",
                "docs/cadence/business-memory.md",
                "codex_cadence/candidates.py",
                "codex_cadence/cli.py",
            ):
                source = ROOT / relative
                target = tmp_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            business_memory = tmp_root / "docs" / "cadence" / "business-memory.md"
            business_memory.write_text(
                business_memory.read_text(encoding="utf-8").replace(
                    "`active`, `fulfilled`, or `superseded`",
                    "`active`, `fulfilled`, `closed`, or `superseded`",
                ),
                encoding="utf-8",
            )

            original_root = validator.ROOT
            validator.ROOT = tmp_root
            try:
                errors = []
                validator.validate_business_memory_contract(errors)
            finally:
                validator.ROOT = original_root

        self.assertTrue(
            any(
                "docs/cadence/business-memory.md business-memory status values must exactly match" in error
                for error in errors
            ),
            f"expected status taxonomy error, got {errors}",
        )

    def test_skill_create_handoff_example_declares_task_type(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        match = re.search(r"python scripts/cadence\.py create-handoff .+", text)
        self.assertIsNotNone(match, "missing create-handoff example")
        self.assertIn("--task-type", match.group(0))

    def test_prepare_handoff_docs_describe_stop_packet_and_host_signal_boundary(self):
        for relative in ("README.md", "SKILL.md", "docs/protocol.md"):
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("prepare-handoff", text)
                self.assertIn("stop_current_session", text)
                self.assertIn("automatic context detection requires a host/session signal", text.lower())


if __name__ == "__main__":
    unittest.main()
