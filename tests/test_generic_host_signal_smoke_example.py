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
            [
                "no-signal.json",
                "context-pressure.json",
                "reviewer-loop.json",
                "ci-loop.json",
                "operator-stop.json",
            ],
        )
        scenarios = {scenario["fixture"]: scenario for scenario in summary["scenarios"]}

        no_signal = scenarios["no-signal.json"]
        self.assertEqual(no_signal["adapter_result"], "no_handoff_needed")
        self.assertFalse(no_signal["cadence_called"])
        self.assertFalse(no_signal["stop_current_session"])
        self.assertEqual(no_signal["packets"], {})

        for fixture_name, expected_guardrail in {
            "context-pressure.json": "context",
            "reviewer-loop.json": "reviewer_loop",
            "ci-loop.json": "ci_loop",
            "operator-stop.json": "operator_stop",
        }.items():
            with self.subTest(fixture_name=fixture_name):
                scenario = scenarios[fixture_name]
                fixture = json.loads((FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))
                self.assertEqual(scenario["adapter_result"], "handoff_prepared")
                self.assertTrue(scenario["cadence_called"])
                self.assertTrue(scenario["stop_current_session"])
                self.assertEqual(scenario["observed_guardrail"], expected_guardrail)
                for key in ("observed_summary", "observed_task_type", "observed_drivers", "observed_next_action"):
                    self.assertIn(key, scenario)
                self.assertEqual(scenario["observed_summary"], fixture["summary"])
                self.assertEqual(scenario["observed_task_type"], fixture["task_type"])
                self.assertEqual(scenario["observed_drivers"], fixture["drivers"])
                self.assertEqual(scenario["observed_next_action"], fixture["next_action"])
                self.assertEqual(scenario["packets"]["status"]["cadence"]["state"], "PLAY_ON")
                self.assertEqual(scenario["packets"]["prepare_handoff"]["handoff"]["status"], "READY")
                self.assertEqual(scenario["packets"]["prepare_handoff"]["handoff"]["guardrail"], expected_guardrail)

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
        self.assertEqual(
            list(cases),
            [
                "no-signal.json",
                "context-pressure.json",
                "reviewer-loop.json",
                "ci-loop.json",
                "operator-stop.json",
            ],
        )
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

        for fixture_name, kind, expected_guardrail in (
            ("context-pressure.json", "context_pressure", "context"),
            ("reviewer-loop.json", "reviewer_loop", "reviewer_loop"),
            ("ci-loop.json", "ci_loop", "ci_loop"),
            ("operator-stop.json", "operator_stop", "operator_stop"),
        ):
            with self.subTest(fixture_name=fixture_name):
                normalized = cases[fixture_name]["normalized_behavior"]
                self.assertEqual(normalized["signal_kind"], kind)
                self.assertEqual(normalized["signal_confidence"], "high")
                self.assertEqual(normalized["observed_guardrail"], expected_guardrail)
                self.assertEqual(normalized["packet_keys"], ["prepare_handoff", "status"])
                self.assertEqual(normalized["prepared_handoff_status"], "READY")
                self.assertTrue(normalized["prepare_stop_current_session"])

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
        reviewer_row = next(row for row in table_rows if '`kind: "reviewer_loop"`' in row)
        ci_row = next(row for row in table_rows if '`kind: "ci_loop"`' in row)
        operator_row = next(row for row in table_rows if '`kind: "operator_stop"`' in row)
        no_signal_row = next(row for row in table_rows if "no stop or handoff signal" in row)
        self.assertIn("`--guardrail context`", context_row)
        self.assertNotIn("`--guardrail operator_stop`", context_row)
        self.assertIn("`--guardrail reviewer_loop`", reviewer_row)
        self.assertIn("`--guardrail ci_loop`", ci_row)
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
