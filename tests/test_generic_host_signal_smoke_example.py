import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "examples" / "generic-host-signal" / "run.py"
FIXTURE_DIR = ROOT / "examples" / "adapter-template" / "host-signal-fixtures"
MAPPING_DOC = ROOT / "examples" / "adapter-template" / "host-binding-mapping.md"


class GenericHostSignalSmokeExampleTests(unittest.TestCase):
    def git_only_path(self):
        git = shutil.which("git")
        self.assertIsNotNone(git, "git must be available for fixture repo setup")
        return str(Path(git).resolve().parent)

    def load_smoke_module(self):
        spec = importlib.util.spec_from_file_location("generic_host_signal_run", SMOKE_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        return module

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
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("examples/generic-host-signal/run.py", adapters)
        self.assertIn("generic host-signal smoke", roadmap)
        for text in (adapters, roadmap, readme):
            self.assertIn("--parity-contract", text)

    def test_generic_host_signal_local_helpers_are_safe_and_predictable(self):
        smoke = self.load_smoke_module()

        with mock.patch.dict(os.environ, {"AGENTIC_CADENCE_PYTHON": "env-python"}):
            self.assertEqual(
                smoke.cadence_command_value("arg-python", "custom-cadence"),
                '"arg-python" -m codex_cadence',
            )
            self.assertEqual(smoke.cadence_command_value(None, "custom-cadence"), "custom-cadence")
            self.assertEqual(smoke.cadence_command_value(None, None), '"env-python" -m codex_cadence')
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(smoke.cadence_command_value(None, None), "agentic-cadence")

    def test_generic_host_signal_smoke_runs_fixture_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
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
                    timeout=180,
                )
            except subprocess.TimeoutExpired as exc:
                self.fail(
                    "generic host-signal smoke timed out after "
                    f"{exc.timeout}s\nstdout:\n{exc.stdout}\nstderr:\n{exc.stderr}"
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

    def test_generic_host_signal_parity_contract_matches_shell_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SMOKE_SCRIPT),
                    "--parity-contract",
                    "--work-dir",
                    str(Path(tmp) / "parity-work"),
                    "--cadence-python",
                    sys.executable,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=300,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["result"], "generic_host_signal_shell_parity_contract_passed")
        self.assertIn("not a real host adapter", summary["contract_note"])
        self.assertIn("generic host-signal smoke", summary["contract_note"])
        self.assertIn("generic shell host-binding replay contract", summary["contract_note"])

        cases = {case["signal_fixture"]: case for case in summary["parity_cases"]}
        self.assertEqual(list(cases), ["no-signal.json", "context-pressure.json", "operator-stop.json"])
        for case in cases.values():
            self.assertTrue(case["consistent"])
            self.assertEqual(case["normalized_behavior"], case["path_results"]["generic_host_signal"])
            self.assertEqual(case["normalized_behavior"], case["path_results"]["generic_shell_host_binding"])

        no_signal = cases["no-signal.json"]["normalized_behavior"]
        self.assertIsNone(no_signal["signal_kind"])
        self.assertIsNone(no_signal["signal_confidence"])
        self.assertEqual(no_signal["adapter_result"], "no_handoff_needed")
        self.assertFalse(no_signal["cadence_called"])
        self.assertEqual(no_signal["packet_keys"], [])

        context_pressure = cases["context-pressure.json"]["normalized_behavior"]
        self.assertEqual(context_pressure["signal_kind"], "context_pressure")
        self.assertEqual(context_pressure["signal_confidence"], "high")
        self.assertEqual(context_pressure["observed_guardrail"], "context")
        self.assertEqual(context_pressure["packet_keys"], ["prepare_handoff", "status"])
        self.assertEqual(context_pressure["prepared_handoff_status"], "READY")
        self.assertTrue(context_pressure["prepare_stop_current_session"])

        operator_stop = cases["operator-stop.json"]["normalized_behavior"]
        self.assertEqual(operator_stop["signal_kind"], "operator_stop")
        self.assertEqual(operator_stop["signal_confidence"], "high")
        self.assertEqual(operator_stop["observed_guardrail"], "operator_stop")
        self.assertEqual(operator_stop["packet_keys"], ["prepare_handoff", "status"])
        self.assertEqual(operator_stop["prepared_handoff_status"], "READY")
        self.assertTrue(operator_stop["prepare_stop_current_session"])

    def test_generic_host_signal_parity_contract_preserves_env_python_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PATH"] = self.git_only_path()
            env["AGENTIC_CADENCE_PYTHON"] = sys.executable
            result = subprocess.run(
                [
                    sys.executable,
                    str(SMOKE_SCRIPT),
                    "--parity-contract",
                    "--work-dir",
                    str(Path(tmp) / "parity-env-work"),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=300,
                env=env,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["result"], "generic_host_signal_shell_parity_contract_passed")

    def test_generic_host_signal_parity_contract_prefers_explicit_command_over_env_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PATH"] = self.git_only_path()
            env["AGENTIC_CADENCE_PYTHON"] = "definitely-missing-cadence-python"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SMOKE_SCRIPT),
                    "--parity-contract",
                    "--work-dir",
                    str(Path(tmp) / "parity-command-work"),
                    "--cadence-command",
                    f'"{sys.executable}" -m codex_cadence',
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=300,
                env=env,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["result"], "generic_host_signal_shell_parity_contract_passed")

    def test_generic_host_signal_smoke_runs_in_package_matrix(self):
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")

        self.assertIn("Run generic host-signal smoke example", workflow)
        self.assertIn("python examples/generic-host-signal/run.py --cadence-python python", workflow)
        self.assertIn("Run generic host/shell parity contract", workflow)
        self.assertIn("python examples/generic-host-signal/run.py --parity-contract --cadence-python python", workflow)

    def test_host_binding_mapping_example_documents_future_binding_boundary(self):
        self.assertTrue(MAPPING_DOC.exists(), "missing host-binding mapping example")

        mapping = MAPPING_DOC.read_text(encoding="utf-8")
        self.assertIn("Host Binding Mapping Example", mapping)
        self.assertIn("examples/generic-host-signal/run.py", mapping)
        self.assertIn("--parity-contract", mapping)

        table_rows = [line for line in mapping.splitlines() if line.startswith("| The ")]
        context_row = next(row for row in table_rows if '`kind: "context_pressure"`' in row)
        operator_row = next(row for row in table_rows if '`kind: "operator_stop"`' in row)
        no_signal_row = next(row for row in table_rows if "no stop or handoff signal" in row)
        self.assertIn("`--guardrail context`", context_row)
        self.assertNotIn("`--guardrail operator_stop`", context_row)
        self.assertIn("`--guardrail operator_stop`", operator_row)
        self.assertNotIn("`--guardrail context`", operator_row)
        self.assertIn("no Cadence call", no_signal_row)

        lower_mapping = mapping.lower()
        self.assertIn("adapter-local fields", lower_mapping)
        self.assertIn("not a real host integration", lower_mapping)
        self.assertIn("no claude or gemini adapter is shipped", lower_mapping)

        adapters = (ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        self.assertIn("examples/adapter-template/host-binding-mapping.md", adapters)
        self.assertIn("host-binding mapping example", roadmap)


if __name__ == "__main__":
    unittest.main()
