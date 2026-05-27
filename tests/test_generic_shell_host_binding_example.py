import ast
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SHELL_BINDING_SCRIPT = ROOT / "examples" / "generic-shell-host-binding" / "run.py"
HOST_EVENT_DIR = ROOT / "examples" / "generic-shell-host-binding" / "host-events"


class GenericShellHostBindingExampleTests(unittest.TestCase):
    def load_shell_binding_module(self):
        spec = importlib.util.spec_from_file_location("generic_shell_host_binding_run", SHELL_BINDING_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        return module

    def load_event(self, filename):
        return json.loads((HOST_EVENT_DIR / filename).read_text(encoding="utf-8"))

    def run_shell_binding_event_file(self, event_payload, work_dir, event_filename=None, encoding="utf-8"):
        event_path = work_dir.parent / (event_filename or f"{work_dir.name}.json")
        event_path.write_text(json.dumps(event_payload), encoding=encoding)
        return subprocess.run(
            [
                sys.executable,
                str(SHELL_BINDING_SCRIPT),
                "--host-event-file",
                str(event_path),
                "--work-dir",
                str(work_dir),
                "--cadence-python",
                sys.executable,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )

    def run_shell_binding_stdin(self, event_payload, work_dir, input_text=None):
        stdin_text = json.dumps(event_payload) if input_text is None else input_text
        return subprocess.run(
            [
                sys.executable,
                str(SHELL_BINDING_SCRIPT),
                "--host-event-stdin",
                "--work-dir",
                str(work_dir),
                "--cadence-python",
                sys.executable,
            ],
            cwd=ROOT,
            text=True,
            input=stdin_text,
            capture_output=True,
            check=False,
            timeout=180,
        )

    def test_generic_shell_binding_uses_public_boundaries_only(self):
        self.assertTrue(SHELL_BINDING_SCRIPT.exists(), "missing generic shell host-binding example")
        source = SHELL_BINDING_SCRIPT.read_text(encoding="utf-8")
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
        self.assertNotIn("__import__", source)
        self.assertNotIn("importlib", source)
        self.assertIn("subprocess.run", source)
        self.assertIn("examples/adapter-template/adapter.py", source)
        self.assertIn("host-binding-mapping.md", source)

        adapters = (ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        mapping = (ROOT / "examples" / "adapter-template" / "host-binding-mapping.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("examples/generic-shell-host-binding/run.py", adapters)
        self.assertIn("examples/generic-shell-host-binding/run.py", mapping)
        self.assertIn("generic shell host-binding", roadmap)
        for text in (adapters, mapping, roadmap, readme):
            self.assertIn("--host-event-file", text)
            self.assertIn("--host-event-stdin", text)
            self.assertIn("file-backed", text)

    def test_generic_shell_binding_local_helpers_are_safe_and_predictable(self):
        shell_binding = self.load_shell_binding_module()

        with mock.patch.dict(os.environ, {"AGENTIC_CADENCE_PYTHON": "env-python"}):
            self.assertEqual(
                shell_binding.cadence_command_value("arg-python", "custom-cadence"),
                '"arg-python" -m codex_cadence',
            )
            self.assertEqual(shell_binding.cadence_command_value(None, "custom-cadence"), "custom-cadence")
            self.assertEqual(shell_binding.cadence_command_value(None, None), '"env-python" -m codex_cadence')
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(shell_binding.cadence_command_value(None, None), "agentic-cadence")

        valid_event = {
            "event": "context_pressure",
            "source": "generic-shell-host-binding",
            "confidence": "high",
            "summary": "Summary.",
            "task_type": "execution",
            "drivers": ["multiple_files"],
            "next_action": "Next action.",
        }
        self.assertEqual(shell_binding.map_host_event_to_signal(valid_event)["drivers"], ["multiple_files"])
        with self.assertRaisesRegex(RuntimeError, "unsupported host event driver"):
            shell_binding.map_host_event_to_signal({**valid_event, "drivers": ["not_a_driver"]})
        with self.assertRaisesRegex(RuntimeError, "non-empty strings"):
            shell_binding.map_host_event_to_signal({**valid_event, "drivers": [""]})
        with self.assertRaisesRegex(RuntimeError, "source must be"):
            shell_binding.map_host_event_to_signal({**valid_event, "source": "x" * 65})

        class CodepageDecodedStdin:
            def __init__(self, payload):
                self.buffer = io.BytesIO(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))

            def read(self):
                return self.buffer.read().decode("cp1252")

        with mock.patch.object(sys, "stdin", CodepageDecodedStdin(valid_event)):
            self.assertEqual(shell_binding.load_host_event_stdin()["event"], "context_pressure")

        with tempfile.TemporaryDirectory() as tmp:
            custom_existing = Path(tmp) / "custom-existing"
            custom_existing.mkdir()
            with self.assertRaisesRegex(RuntimeError, "refusing to remove custom work directory"):
                shell_binding.prepare_work_dir(custom_existing, replace_existing=True)

            managed = Path(tmp) / "managed"
            shell_binding.prepare_work_dir(managed, replace_existing=False)
            marker = managed / shell_binding.WORK_DIR_MARKER
            self.assertTrue(marker.is_file())
            stale_file = managed / "stale.txt"
            stale_file.write_text("stale\n", encoding="utf-8")
            shell_binding.prepare_work_dir(managed, replace_existing=True)
            self.assertTrue(marker.is_file())
            self.assertFalse(stale_file.exists())

    def test_generic_shell_binding_maps_host_events_to_public_cli_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SHELL_BINDING_SCRIPT),
                        "--work-dir",
                        str(Path(tmp) / "work"),
                        "--cadence-python",
                        sys.executable,
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=180,
                )
            except subprocess.TimeoutExpired as exc:
                self.fail(
                    "generic shell host-binding stub timed out after "
                    f"{exc.timeout}s\nstdout:\n{exc.stdout}\nstderr:\n{exc.stderr}"
                )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["result"], "generic_shell_host_binding_stub_passed")
        self.assertIn("not a real host adapter", summary["host_binding_note"])
        self.assertEqual(
            [scenario["host_event_file"] for scenario in summary["scenarios"]],
            ["no-event.json", "context-pressure.json", "operator-stop.json"],
        )

        no_event = summary["scenarios"][0]
        self.assertIsNone(no_event["host_event"])
        self.assertIsNone(no_event["mapped_signal_kind"])
        self.assertEqual(no_event["adapter_result"], "no_handoff_needed")
        self.assertFalse(no_event["cadence_called"])
        self.assertEqual(no_event["packets"], {})

        context_pressure = summary["scenarios"][1]
        context_event = self.load_event("context-pressure.json")
        self.assertEqual(context_pressure["host_event"], "context_pressure")
        self.assertEqual(context_pressure["mapped_signal_kind"], "context_pressure")
        self.assertEqual(context_pressure["adapter_result"], "handoff_prepared")
        self.assertTrue(context_pressure["cadence_called"])
        self.assertTrue(context_pressure["stop_current_session"])
        self.assertTrue(context_pressure["packets"]["prepare_handoff"]["stop_current_session"])
        self.assertEqual(context_pressure["packets"]["prepare_handoff"]["handoff"]["status"], "READY")
        self.assertEqual(context_pressure["observed_guardrail"], "context")
        self.assertEqual(context_pressure["observed_summary"], context_event["summary"])
        self.assertEqual(context_pressure["observed_task_type"], context_event["task_type"])
        self.assertEqual(context_pressure["observed_drivers"], context_event["drivers"])
        self.assertEqual(context_pressure["observed_next_action"], context_event["next_action"])

        operator_stop = summary["scenarios"][2]
        operator_event = self.load_event("operator-stop.json")
        self.assertEqual(operator_stop["host_event"], "operator_stop")
        self.assertEqual(operator_stop["mapped_signal_kind"], "operator_stop")
        self.assertEqual(operator_stop["adapter_result"], "handoff_prepared")
        self.assertTrue(operator_stop["cadence_called"])
        self.assertTrue(operator_stop["stop_current_session"])
        self.assertTrue(operator_stop["packets"]["prepare_handoff"]["stop_current_session"])
        self.assertEqual(operator_stop["packets"]["prepare_handoff"]["handoff"]["status"], "READY")
        self.assertEqual(operator_stop["observed_guardrail"], "operator_stop")
        self.assertEqual(operator_stop["observed_summary"], operator_event["summary"])
        self.assertEqual(operator_stop["observed_task_type"], operator_event["task_type"])
        self.assertEqual(operator_stop["observed_drivers"], operator_event["drivers"])
        self.assertEqual(operator_stop["observed_next_action"], operator_event["next_action"])

    def test_generic_shell_binding_reads_external_host_event_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            context_result = self.run_shell_binding_event_file(
                self.load_event("context-pressure.json"),
                tmp_root / "context-work",
                event_filename="context pressure event.json",
                encoding="utf-8-sig",
            )
            operator_result = self.run_shell_binding_event_file(
                self.load_event("operator-stop.json"),
                tmp_root / "operator-work",
            )
            no_event_result = self.run_shell_binding_event_file(None, tmp_root / "no-event-work")

        self.assertEqual(context_result.returncode, 0, context_result.stderr)
        context = json.loads(context_result.stdout)
        self.assertEqual(context["result"], "generic_shell_host_binding_event_passed")
        context_scenario = context["scenario"]
        self.assertEqual(context_scenario["host_event"], "context_pressure")
        self.assertEqual(context_scenario["mapped_signal_kind"], "context_pressure")
        self.assertEqual(context_scenario["adapter_result"], "handoff_prepared")
        self.assertTrue(context_scenario["cadence_called"])
        self.assertTrue(context_scenario["stop_current_session"])
        self.assertEqual(context_scenario["observed_guardrail"], "context")
        self.assertEqual(context_scenario["packets"]["prepare_handoff"]["handoff"]["status"], "READY")

        self.assertEqual(operator_result.returncode, 0, operator_result.stderr)
        operator = json.loads(operator_result.stdout)
        self.assertEqual(operator["result"], "generic_shell_host_binding_event_passed")
        operator_scenario = operator["scenario"]
        self.assertEqual(operator_scenario["host_event"], "operator_stop")
        self.assertEqual(operator_scenario["mapped_signal_kind"], "operator_stop")
        self.assertEqual(operator_scenario["adapter_result"], "handoff_prepared")
        self.assertTrue(operator_scenario["cadence_called"])
        self.assertTrue(operator_scenario["stop_current_session"])
        self.assertEqual(operator_scenario["observed_guardrail"], "operator_stop")
        self.assertEqual(operator_scenario["packets"]["prepare_handoff"]["handoff"]["status"], "READY")

        self.assertEqual(no_event_result.returncode, 0, no_event_result.stderr)
        no_event = json.loads(no_event_result.stdout)
        self.assertEqual(no_event["result"], "generic_shell_host_binding_event_passed")
        no_event_scenario = no_event["scenario"]
        self.assertIsNone(no_event_scenario["host_event"])
        self.assertIsNone(no_event_scenario["mapped_signal_kind"])
        self.assertEqual(no_event_scenario["adapter_result"], "no_handoff_needed")
        self.assertFalse(no_event_scenario["cadence_called"])
        self.assertEqual(no_event_scenario["packets"], {})

    def test_generic_shell_binding_reads_external_host_event_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            context_result = self.run_shell_binding_stdin(
                self.load_event("context-pressure.json"),
                tmp_root / "context-work",
            )
            operator_result = self.run_shell_binding_stdin(
                self.load_event("operator-stop.json"),
                tmp_root / "operator-work",
            )
            no_event_result = self.run_shell_binding_stdin(None, tmp_root / "no-event-work")

        self.assertEqual(context_result.returncode, 0, context_result.stderr)
        context = json.loads(context_result.stdout)
        self.assertEqual(context["result"], "generic_shell_host_binding_event_passed")
        self.assertEqual(context["host_event_source"], "stdin")
        context_scenario = context["scenario"]
        self.assertEqual(context_scenario["host_event_file"], "<stdin>")
        self.assertEqual(context_scenario["host_event"], "context_pressure")
        self.assertEqual(context_scenario["mapped_signal_kind"], "context_pressure")
        self.assertEqual(context_scenario["adapter_result"], "handoff_prepared")
        self.assertTrue(context_scenario["cadence_called"])
        self.assertTrue(context_scenario["stop_current_session"])
        self.assertEqual(context_scenario["observed_guardrail"], "context")
        self.assertEqual(context_scenario["packets"]["prepare_handoff"]["handoff"]["status"], "READY")

        self.assertEqual(operator_result.returncode, 0, operator_result.stderr)
        operator = json.loads(operator_result.stdout)
        self.assertEqual(operator["result"], "generic_shell_host_binding_event_passed")
        self.assertEqual(operator["host_event_source"], "stdin")
        operator_scenario = operator["scenario"]
        self.assertEqual(operator_scenario["host_event_file"], "<stdin>")
        self.assertEqual(operator_scenario["host_event"], "operator_stop")
        self.assertEqual(operator_scenario["mapped_signal_kind"], "operator_stop")
        self.assertEqual(operator_scenario["adapter_result"], "handoff_prepared")
        self.assertTrue(operator_scenario["cadence_called"])
        self.assertTrue(operator_scenario["stop_current_session"])
        self.assertEqual(operator_scenario["observed_guardrail"], "operator_stop")
        self.assertEqual(operator_scenario["packets"]["prepare_handoff"]["handoff"]["status"], "READY")

        self.assertEqual(no_event_result.returncode, 0, no_event_result.stderr)
        no_event = json.loads(no_event_result.stdout)
        self.assertEqual(no_event["result"], "generic_shell_host_binding_event_passed")
        self.assertEqual(no_event["host_event_source"], "stdin")
        no_event_scenario = no_event["scenario"]
        self.assertEqual(no_event_scenario["host_event_file"], "<stdin>")
        self.assertIsNone(no_event_scenario["host_event"])
        self.assertIsNone(no_event_scenario["mapped_signal_kind"])
        self.assertEqual(no_event_scenario["adapter_result"], "no_handoff_needed")
        self.assertFalse(no_event_scenario["cadence_called"])
        self.assertEqual(no_event_scenario["packets"], {})

    def test_generic_shell_binding_external_host_event_file_errors_are_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            missing = subprocess.run(
                [
                    sys.executable,
                    str(SHELL_BINDING_SCRIPT),
                    "--host-event-file",
                    str(tmp_root / "missing.json"),
                    "--work-dir",
                    str(tmp_root / "missing-work"),
                    "--cadence-python",
                    sys.executable,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
            )
            invalid_path = tmp_root / "invalid.json"
            invalid_path.write_text("{not-json", encoding="utf-8")
            invalid = subprocess.run(
                [
                    sys.executable,
                    str(SHELL_BINDING_SCRIPT),
                    "--host-event-file",
                    str(invalid_path),
                    "--work-dir",
                    str(tmp_root / "invalid-work"),
                    "--cadence-python",
                    sys.executable,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
            )

        self.assertEqual(missing.returncode, 1)
        self.assertIn("host event file could not be read", missing.stderr)
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("host event file is not valid JSON", invalid.stderr)

    def test_generic_shell_binding_external_host_event_stdin_errors_are_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            invalid = self.run_shell_binding_stdin(
                None,
                tmp_root / "invalid-stdin-work",
                input_text="{not-json",
            )
            event_path = tmp_root / "event.json"
            event_path.write_text(json.dumps(self.load_event("context-pressure.json")), encoding="utf-8")
            conflict = subprocess.run(
                [
                    sys.executable,
                    str(SHELL_BINDING_SCRIPT),
                    "--host-event-file",
                    str(event_path),
                    "--host-event-stdin",
                    "--work-dir",
                    str(tmp_root / "conflict-work"),
                    "--cadence-python",
                    sys.executable,
                ],
                cwd=ROOT,
                text=True,
                input=json.dumps(self.load_event("operator-stop.json")),
                capture_output=True,
                check=False,
                timeout=180,
            )

        self.assertEqual(invalid.returncode, 1)
        self.assertIn("host event stdin is not valid JSON", invalid.stderr)
        self.assertEqual(conflict.returncode, 2)
        self.assertIn("not allowed with argument", conflict.stderr)

    def test_generic_shell_binding_runs_in_package_matrix(self):
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        package_section = workflow.split("\n  package:", 1)[1]

        self.assertIn("Run generic shell host-binding stub example", package_section)
        self.assertIn("python examples/generic-shell-host-binding/run.py --cadence-python python", package_section)
        self.assertIn("examples/generic-shell-host-binding/work/", ignore_text)


if __name__ == "__main__":
    unittest.main()
