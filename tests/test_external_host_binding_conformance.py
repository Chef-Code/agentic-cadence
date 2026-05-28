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
    def make_windows_junction(self, target: Path, junction: Path) -> None:
        if os.name != "nt":
            self.skipTest("Windows junctions are unavailable")
        if not hasattr(junction, "is_junction"):
            self.skipTest("Path.is_junction is unavailable")

        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            self.skipTest(f"directory junctions are unavailable: {detail}")
        if not junction.is_junction():
            self.skipTest("created path is not reported as a junction")

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
        calls_subprocess_run = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "run"
            for node in ast.walk(tree)
        )
        module = load_conformance_module()
        parsed = module.build_parser().parse_args(["--binding-command-template", "binding {host_event_file}"])

        self.assertTrue(calls_subprocess_run)
        self.assertEqual(module.SHELL_BINDING_DISPLAY, "examples/generic-shell-host-binding/run.py")
        self.assertEqual(module.SCHEMA_SCRIPT.name, "host_signal_contract.py")
        self.assertEqual(parsed.binding_command_template, "binding {host_event_file}")

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

    def test_external_conformance_cadence_args_placeholder_round_trips_spaces(self):
        module = load_conformance_module()
        cadence_python = "C:/Program Files/Python/python.exe" if os.name == "nt" else "/tmp/Python With Spaces/python"
        joined = module.join_binding_command_args(["--cadence-python", cadence_python])
        command = module.split_binding_command_template(f"binding {joined}")

        self.assertEqual(command, ["binding", "--cadence-python", cadence_python])

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

    def test_external_conformance_refuses_junction_work_dir_replacement(self):
        module = load_conformance_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "target"
            target.mkdir()
            sentinel = target / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            junction = tmp_path / "work-junction"
            self.make_windows_junction(target, junction)

            with self.assertRaisesRegex(RuntimeError, "junction work directory"):
                module.prepare_work_dir(junction, replace_existing=True)

            self.assertTrue(sentinel.exists())

    def test_external_conformance_refuses_parent_traversal_work_dir_replacement(self):
        module = load_conformance_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            script_dir = tmp_path / "script"
            work_dir = script_dir / "work"
            work_dir.mkdir(parents=True)
            sentinel = script_dir / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            original_default = module.DEFAULT_WORK_DIR
            original_script = module.SCRIPT_DIR
            try:
                module.DEFAULT_WORK_DIR = work_dir
                module.SCRIPT_DIR = script_dir
                with self.assertRaisesRegex(RuntimeError, "unsafe work directory target"):
                    module.prepare_work_dir(work_dir / "..", replace_existing=True)
            finally:
                module.DEFAULT_WORK_DIR = original_default
                module.SCRIPT_DIR = original_script

            self.assertTrue(sentinel.exists())

    def test_external_conformance_refuses_symlink_parent_work_dir_replacement(self):
        module = load_conformance_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            default_work = tmp_path / "work"
            outside = tmp_path / "outside"
            external_dir = outside / "external-dir"
            default_work.mkdir()
            external_dir.mkdir(parents=True)
            sentinel = external_dir / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            link = default_work / "link"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            original_default = module.DEFAULT_WORK_DIR
            try:
                module.DEFAULT_WORK_DIR = default_work
                with self.assertRaisesRegex(RuntimeError, "symlink path component"):
                    module.prepare_work_dir(link / "external-dir", replace_existing=True)
            finally:
                module.DEFAULT_WORK_DIR = original_default

            self.assertTrue(sentinel.exists())

    def test_external_conformance_refuses_junction_parent_work_dir_replacement(self):
        module = load_conformance_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            default_work = tmp_path / "work"
            outside = tmp_path / "outside"
            external_dir = outside / "external-dir"
            default_work.mkdir()
            external_dir.mkdir(parents=True)
            sentinel = external_dir / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            junction = default_work / "junction"
            self.make_windows_junction(outside, junction)

            original_default = module.DEFAULT_WORK_DIR
            try:
                module.DEFAULT_WORK_DIR = default_work
                with self.assertRaisesRegex(RuntimeError, "junction path component"):
                    module.prepare_work_dir(junction / "external-dir", replace_existing=True)
            finally:
                module.DEFAULT_WORK_DIR = original_default

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

        self.assertNotEqual(result.returncode, 0)
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
        for text in (readme, adapters, mapping):
            self.assertIn('"{host_event_file}"', text)
            self.assertIn('"{case_work_dir}"', text)
        self.assertIn("Run external host-binding conformance harness", workflow)
        self.assertIn("external-host-binding-conformance/work/", ignore_text)


if __name__ == "__main__":
    unittest.main()
