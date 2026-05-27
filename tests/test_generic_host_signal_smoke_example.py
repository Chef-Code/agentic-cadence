import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "examples" / "generic-host-signal" / "run.py"
FIXTURE_DIR = ROOT / "examples" / "adapter-template" / "host-signal-fixtures"


class GenericHostSignalSmokeExampleTests(unittest.TestCase):
    def test_generic_host_signal_smoke_uses_public_boundaries_only(self):
        self.assertTrue(SMOKE_SCRIPT.exists(), "missing generic host-signal smoke example")
        source = SMOKE_SCRIPT.read_text(encoding="utf-8")
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
        self.assertIn("host-signal-fixtures", source)

        adapters = (ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        self.assertIn("examples/generic-host-signal/run.py", adapters)
        self.assertIn("generic host-signal smoke", roadmap)

    def test_generic_host_signal_smoke_runs_fixture_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SMOKE_SCRIPT),
                    "--work-dir",
                    str(Path(tmp) / "work"),
                    "--cadence-python",
                    sys.executable,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)

        self.assertEqual(summary["result"], "generic_host_signal_smoke_passed")
        self.assertEqual(
            [scenario["fixture"] for scenario in summary["scenarios"]],
            ["no-signal.json", "context-pressure.json", "operator-stop.json"],
        )

        no_signal = summary["scenarios"][0]
        self.assertEqual(no_signal["adapter_result"], "no_handoff_needed")
        self.assertFalse(no_signal["cadence_called"])
        self.assertFalse(no_signal["stop_current_session"])
        self.assertEqual(no_signal["packets"], {})

        context_pressure = summary["scenarios"][1]
        context_fixture = json.loads((FIXTURE_DIR / "context-pressure.json").read_text(encoding="utf-8"))
        self.assertEqual(context_pressure["adapter_result"], "handoff_prepared")
        self.assertTrue(context_pressure["cadence_called"])
        self.assertTrue(context_pressure["stop_current_session"])
        self.assertEqual(context_pressure["observed_guardrail"], "context")
        for key in ("observed_summary", "observed_task_type", "observed_drivers", "observed_next_action"):
            self.assertIn(key, context_pressure)
        self.assertEqual(context_pressure["observed_summary"], context_fixture["summary"])
        self.assertEqual(context_pressure["observed_task_type"], context_fixture["task_type"])
        self.assertEqual(context_pressure["observed_drivers"], context_fixture["drivers"])
        self.assertEqual(context_pressure["observed_next_action"], context_fixture["next_action"])
        self.assertEqual(context_pressure["packets"]["status"]["cadence"]["state"], "PLAY_ON")
        self.assertEqual(context_pressure["packets"]["prepare_handoff"]["handoff"]["status"], "READY")
        self.assertEqual(context_pressure["packets"]["prepare_handoff"]["handoff"]["guardrail"], "context")

        operator_stop = summary["scenarios"][2]
        operator_fixture = json.loads((FIXTURE_DIR / "operator-stop.json").read_text(encoding="utf-8"))
        self.assertEqual(operator_stop["adapter_result"], "handoff_prepared")
        self.assertTrue(operator_stop["cadence_called"])
        self.assertTrue(operator_stop["stop_current_session"])
        self.assertEqual(operator_stop["observed_guardrail"], "operator_stop")
        for key in ("observed_summary", "observed_task_type", "observed_drivers", "observed_next_action"):
            self.assertIn(key, operator_stop)
        self.assertEqual(operator_stop["observed_summary"], operator_fixture["summary"])
        self.assertEqual(operator_stop["observed_task_type"], operator_fixture["task_type"])
        self.assertEqual(operator_stop["observed_drivers"], operator_fixture["drivers"])
        self.assertEqual(operator_stop["observed_next_action"], operator_fixture["next_action"])
        self.assertEqual(operator_stop["packets"]["status"]["cadence"]["state"], "PLAY_ON")
        self.assertEqual(operator_stop["packets"]["prepare_handoff"]["handoff"]["status"], "READY")
        self.assertEqual(operator_stop["packets"]["prepare_handoff"]["handoff"]["guardrail"], "operator_stop")

    def test_generic_host_signal_smoke_runs_in_package_matrix(self):
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")

        self.assertIn("Run generic host-signal smoke example", workflow)
        self.assertIn("python examples/generic-host-signal/run.py --cadence-python python", workflow)


if __name__ == "__main__":
    unittest.main()
