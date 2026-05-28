import ast
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_SCRIPT = ROOT / "examples" / "external-host-binding-conformance" / "run.py"
SHELL_BINDING_SCRIPT = ROOT / "examples" / "generic-shell-host-binding" / "run.py"


def portable_path(path: Path | str) -> str:
    return Path(path).as_posix()


class ExternalHostBindingConformanceTests(unittest.TestCase):
    def test_external_conformance_uses_public_boundaries_only(self):
        self.assertTrue(CONFORMANCE_SCRIPT.exists(), "missing external host-binding conformance harness")
        source = CONFORMANCE_SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source)

        private_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                private_imports.extend(
                    alias.name for alias in node.names if alias.name.startswith(("codex_cadence", "transmission_control"))
                )
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(("codex_cadence", "transmission_control")):
                    private_imports.append(node.module)

        self.assertEqual(private_imports, [])
        self.assertIn("subprocess.run", source)
        self.assertIn("generic-shell-host-binding", source)
        self.assertIn("host_signal_contract.py", source)
        self.assertIn("binding-command-template", source)

    def test_external_conformance_default_command_matches_generic_shell_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(CONFORMANCE_SCRIPT),
                    "--work-dir",
                    str(Path(tmp) / "work"),
                    "--cadence-python",
                    sys.executable,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=360,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["result"], "external_host_binding_conformance_passed")
        self.assertIn("not a real host adapter", summary["contract_note"])
        self.assertEqual(
            [case["host_event_file"] for case in summary["conformance_cases"]],
            ["no-event.json", "context-pressure.json", "operator-stop.json"],
        )
        for case in summary["conformance_cases"]:
            self.assertTrue(case["consistent"])
            self.assertEqual(case["normalized_behavior"], case["path_results"]["generic_shell_baseline"])
            self.assertEqual(case["normalized_behavior"], case["path_results"]["external_binding"])

    def test_external_conformance_accepts_command_template(self):
        template = (
            f'"{portable_path(sys.executable)}" "{portable_path(SHELL_BINDING_SCRIPT)}" '
            '--host-event-file "{host_event_file}" '
            '--work-dir "{case_work_dir}" '
            f'--cadence-python "{portable_path(sys.executable)}"'
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(CONFORMANCE_SCRIPT),
                    "--work-dir",
                    str(Path(tmp) / "work"),
                    "--binding-command-template",
                    template,
                    "--cadence-python",
                    sys.executable,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=360,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["binding_command_mode"], "template")
        self.assertEqual(summary["result"], "external_host_binding_conformance_passed")

    def test_external_conformance_rejects_mismatched_binding(self):
        mismatch_script = textwrap.dedent(
            """
            import json
            import sys

            host_event_file = sys.argv[sys.argv.index("--host-event-file") + 1]
            print(json.dumps({
                "scenario": {
                    "host_event_file": host_event_file,
                    "host_event": None,
                    "mapped_signal_kind": None,
                    "mapped_signal_confidence": None,
                    "adapter_result": "no_handoff_needed",
                    "cadence_called": False,
                    "observed_guardrail": None,
                    "observed_summary": None,
                    "observed_task_type": None,
                    "observed_drivers": None,
                    "observed_next_action": None,
                    "stop_current_session": False,
                    "packets": {}
                }
            }))
            """
        ).strip()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            script = tmp_path / "mismatched_binding.py"
            script.write_text(mismatch_script, encoding="utf-8")
            template = (
                f'"{portable_path(sys.executable)}" "{portable_path(script)}" '
                '--host-event-file "{host_event_file}" '
                '--work-dir "{case_work_dir}"'
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(CONFORMANCE_SCRIPT),
                    "--work-dir",
                    str(tmp_path / "work"),
                    "--binding-command-template",
                    template,
                    "--cadence-python",
                    sys.executable,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=360,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("context-pressure.json diverged from generic shell baseline", result.stderr)

    def test_external_conformance_is_documented_and_in_ci(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        adapters = (ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        mapping = (ROOT / "examples" / "adapter-template" / "host-binding-mapping.md").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")

        for text in (readme, adapters, roadmap, mapping, workflow):
            self.assertIn("examples/external-host-binding-conformance/run.py", text)
        self.assertIn("Run external host-binding conformance harness", workflow)
        self.assertIn("external-host-binding-conformance/work/", ignore_text)


if __name__ == "__main__":
    unittest.main()
