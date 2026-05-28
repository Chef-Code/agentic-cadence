import ast
import importlib.util
import json
import os
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


def load_conformance_module():
    spec = importlib.util.spec_from_file_location("external_host_binding_conformance_run", CONFORMANCE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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

    @unittest.skipUnless(os.name == "nt", "native Windows command-line parsing is Windows-only")
    def test_external_conformance_accepts_unquoted_windows_template_paths(self):
        module = load_conformance_module()
        command = module.split_binding_command_template(
            r"C:\Tools\binding.exe --host-event-file C:\tmp\event.json --work-dir C:\tmp\work"
        )

        self.assertEqual(command[0], r"C:\Tools\binding.exe")
        self.assertEqual(command[2], r"C:\tmp\event.json")
        self.assertEqual(command[4], r"C:\tmp\work")

    def test_external_conformance_refuses_symlink_work_dir_replacement(self):
        module = load_conformance_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "target"
            target.mkdir()
            sentinel = target / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            link = tmp_path / "work-link"
            try:
                os.symlink(target, link, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "symlink work directory"):
                module.prepare_work_dir(link, replace_existing=True)

            self.assertTrue(sentinel.exists())

    def test_external_conformance_derives_observed_fields_from_packets(self):
        module = load_conformance_module()
        scenario = {
            "host_event": "context_pressure",
            "mapped_signal_kind": "context_pressure",
            "mapped_signal_confidence": "high",
            "adapter_result": "handoff_prepared",
            "cadence_called": False,
            "observed_guardrail": "context",
            "observed_summary": "self-reported summary",
            "observed_task_type": "execution",
            "observed_drivers": ["multiple_files"],
            "observed_next_action": "self-reported next action",
            "stop_current_session": False,
            "packets": {},
        }

        normalized = module.normalized_scenario_behavior(
            scenario,
            expected_behavior={"observed_next_action": "expected next action"},
        )

        self.assertIsNone(normalized["observed_guardrail"])
        self.assertIsNone(normalized["observed_summary"])
        self.assertIsNone(normalized["observed_task_type"])
        self.assertIsNone(normalized["observed_drivers"])
        self.assertIsNone(normalized["observed_next_action"])

    def test_external_conformance_rejects_malformed_packet_shape(self):
        module = load_conformance_module()
        with self.assertRaisesRegex(RuntimeError, "packets must be a JSON object"):
            module.normalized_scenario_behavior(
                {
                    "cadence_called": False,
                    "stop_current_session": False,
                    "packets": [],
                }
            )
        with self.assertRaisesRegex(RuntimeError, "cadence_called must match"):
            module.normalized_scenario_behavior(
                {
                    "cadence_called": True,
                    "stop_current_session": False,
                    "packets": {},
                }
            )

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
