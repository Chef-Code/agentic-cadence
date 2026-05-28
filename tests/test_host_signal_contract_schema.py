import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SCRIPT = ROOT / "examples" / "adapter-template" / "host_signal_contract.py"
HOST_SIGNAL_FIXTURES = ROOT / "examples" / "adapter-template" / "host-signal-fixtures"
HOST_EVENTS = ROOT / "examples" / "generic-shell-host-binding" / "host-events"


def load_contract_module():
    spec = importlib.util.spec_from_file_location("host_signal_contract", CONTRACT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HostSignalContractSchemaTests(unittest.TestCase):
    def test_contract_script_uses_example_boundary_only(self):
        self.assertTrue(CONTRACT_SCRIPT.exists(), "missing host signal contract schema helper")
        source = CONTRACT_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("codex_cadence", source)
        self.assertNotIn("transmission_control", source)
        self.assertIn("host-signal-fixtures", source)
        self.assertIn("generic-shell-host-binding", source)

    def test_contract_cli_validates_checked_in_fixture_pairs(self):
        result = subprocess.run(
            [sys.executable, str(CONTRACT_SCRIPT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["result"], "host_signal_contract_schema_passed")
        self.assertIn("not a real host adapter", summary["contract_note"])
        self.assertEqual(
            [case["host_signal_fixture"] for case in summary["contract_cases"]],
            ["no-signal.json", "context-pressure.json", "operator-stop.json"],
        )
        for case in summary["contract_cases"]:
            self.assertTrue(case["schema_valid"])
            self.assertTrue(case["fixture_pair_aligned"])
            self.assertEqual(case["normalized_host_signal"], case["normalized_host_event"])
        self.assertIsNone(summary["contract_cases"][0]["expected_kind"])
        self.assertIsNone(summary["contract_cases"][0]["normalized_host_signal"])
        self.assertEqual(summary["contract_cases"][1]["expected_kind"], "context_pressure")
        self.assertEqual(summary["contract_cases"][1]["normalized_host_signal"]["kind"], "context_pressure")
        self.assertEqual(summary["contract_cases"][2]["expected_kind"], "operator_stop")
        self.assertEqual(summary["contract_cases"][2]["normalized_host_signal"]["kind"], "operator_stop")

    def test_contract_rejects_extra_or_missing_host_signal_fields(self):
        contract = load_contract_module()
        payload = json.loads((HOST_SIGNAL_FIXTURES / "context-pressure.json").read_text(encoding="utf-8"))

        with self.assertRaisesRegex(RuntimeError, "unsupported fields: unexpected"):
            contract.normalize_host_signal_fixture({**payload, "unexpected": True}, "bad-signal.json")

        missing_summary = dict(payload)
        missing_summary.pop("summary")
        with self.assertRaisesRegex(RuntimeError, "missing fields: summary"):
            contract.normalize_host_signal_fixture(missing_summary, "bad-signal.json")

    def test_contract_rejects_default_case_kind_drift_even_when_pairs_match(self):
        operator_signal = json.loads((HOST_SIGNAL_FIXTURES / "operator-stop.json").read_text(encoding="utf-8"))
        operator_event = json.loads((HOST_EVENTS / "operator-stop.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            signal_dir = tmp_path / "host-signal-fixtures"
            event_dir = tmp_path / "host-events"
            signal_dir.mkdir()
            event_dir.mkdir()
            (signal_dir / "no-signal.json").write_text("null\n", encoding="utf-8")
            (event_dir / "no-event.json").write_text("null\n", encoding="utf-8")
            (signal_dir / "context-pressure.json").write_text("null\n", encoding="utf-8")
            (event_dir / "context-pressure.json").write_text("null\n", encoding="utf-8")
            (signal_dir / "operator-stop.json").write_text(json.dumps(operator_signal), encoding="utf-8")
            (event_dir / "operator-stop.json").write_text(json.dumps(operator_event), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(CONTRACT_SCRIPT),
                    "--host-signal-dir",
                    str(signal_dir),
                    "--host-event-dir",
                    str(event_dir),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("context-pressure.json expected kind 'context_pressure'", result.stderr)

    def test_contract_rejects_whitespace_drift_in_string_fields(self):
        contract = load_contract_module()
        payload = json.loads((HOST_SIGNAL_FIXTURES / "context-pressure.json").read_text(encoding="utf-8"))

        with self.assertRaisesRegex(RuntimeError, "must not have leading or trailing whitespace"):
            contract.normalize_host_signal_fixture({**payload, "summary": f" {payload['summary']}"}, "bad-signal.json")

    def test_contract_rejects_host_event_semantic_drift(self):
        context_signal = json.loads((HOST_SIGNAL_FIXTURES / "context-pressure.json").read_text(encoding="utf-8"))
        context_event = json.loads((HOST_EVENTS / "context-pressure.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            signal_dir = tmp_path / "host-signal-fixtures"
            event_dir = tmp_path / "host-events"
            signal_dir.mkdir()
            event_dir.mkdir()
            (signal_dir / "no-signal.json").write_text("null\n", encoding="utf-8")
            (event_dir / "no-event.json").write_text("null\n", encoding="utf-8")
            (signal_dir / "context-pressure.json").write_text(json.dumps(context_signal), encoding="utf-8")
            drifted = {**context_event, "summary": "Drifted summary."}
            (event_dir / "context-pressure.json").write_text(json.dumps(drifted), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(CONTRACT_SCRIPT),
                    "--host-signal-dir",
                    str(signal_dir),
                    "--host-event-dir",
                    str(event_dir),
                    "--case",
                    "no-signal.json:no-event.json",
                    "--case",
                    "context-pressure.json:context-pressure.json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("context-pressure.json drifted from context-pressure.json", result.stderr)

    def test_contract_schema_is_documented_and_in_ci(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        adapters = (ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        mapping = (ROOT / "examples" / "adapter-template" / "host-binding-mapping.md").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")

        for text in (readme, adapters, mapping, workflow):
            self.assertIn("examples/adapter-template/host_signal_contract.py", text)
        self.assertIn("Run host signal contract schema", workflow)
        package_job = workflow.split("  package:", 1)[1]
        self.assertLess(
            package_job.index("Run host signal contract schema"),
            package_job.index("Run generic host-signal smoke example"),
        )


if __name__ == "__main__":
    unittest.main()
