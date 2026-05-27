import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL_BINDING_SCRIPT = ROOT / "examples" / "generic-shell-host-binding" / "run.py"
HOST_EVENT_DIR = ROOT / "examples" / "generic-shell-host-binding" / "host-events"


class GenericShellHostBindingExampleTests(unittest.TestCase):
    def load_event(self, filename):
        return json.loads((HOST_EVENT_DIR / filename).read_text(encoding="utf-8"))

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
        self.assertIn("subprocess.run", source)
        self.assertIn("examples/adapter-template/adapter.py", source)
        self.assertIn("host-binding-mapping.md", source)

        adapters = (ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        self.assertIn("examples/generic-shell-host-binding/run.py", adapters)
        self.assertIn("generic shell host-binding", roadmap)

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
        self.assertEqual(operator_stop["observed_guardrail"], "operator_stop")
        self.assertEqual(operator_stop["observed_summary"], operator_event["summary"])
        self.assertEqual(operator_stop["observed_task_type"], operator_event["task_type"])
        self.assertEqual(operator_stop["observed_drivers"], operator_event["drivers"])
        self.assertEqual(operator_stop["observed_next_action"], operator_event["next_action"])

    def test_generic_shell_binding_runs_in_package_matrix(self):
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("Run generic shell host-binding stub example", workflow)
        self.assertIn("python examples/generic-shell-host-binding/run.py --cadence-python python", workflow)
        self.assertIn("examples/generic-shell-host-binding/work/", ignore_text)


if __name__ == "__main__":
    unittest.main()
