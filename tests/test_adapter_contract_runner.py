import ast
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNNER_SCRIPT = ROOT / "examples" / "adapter-contract-runner" / "run.py"
SHELL_BINDING_SCRIPT = ROOT / "examples" / "generic-shell-host-binding" / "run.py"


def portable_path(path: Path | str) -> str:
    return Path(path).as_posix()


def load_runner_module():
    spec = importlib.util.spec_from_file_location("adapter_contract_runner", RUNNER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AdapterContractRunnerTests(unittest.TestCase):
    def test_adapter_contract_runner_uses_public_subprocess_boundaries_only(self):
        self.assertTrue(RUNNER_SCRIPT.exists(), "missing adapter contract runner")
        source = RUNNER_SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source)

        private_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                private_imports.extend(
                    alias.name
                    for alias in node.names
                    if alias.name.startswith(("codex_cadence", "transmission_control"))
                )
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(("codex_cadence", "transmission_control")):
                    private_imports.append(node.module)

        self.assertEqual(private_imports, [])
        self.assertIn("subprocess.run", source)
        self.assertIn("not a real host adapter", source)

    def test_adapter_contract_runner_default_command_composes_existing_contracts(self):
        module = load_runner_module()
        expected_results = {
            "host_signal_schema": "host_signal_contract_schema_passed",
            "generic_host_signal_smoke": "generic_host_signal_smoke_passed",
            "generic_shell_replay": "generic_shell_host_binding_replay_contract_passed",
            "generic_host_shell_parity": "generic_host_signal_shell_parity_contract_passed",
            "external_host_binding_conformance": "external_host_binding_conformance_passed",
        }
        with tempfile.TemporaryDirectory() as tmp:
            args = module.build_parser().parse_args(
                ["--work-dir", str(Path(tmp) / "work"), "--cadence-python", sys.executable]
            )
            calls = []

            def fake_run_json_contract(label, command):
                calls.append((label, command))
                return {
                    "label": label,
                    "command": command,
                    "result": expected_results[label],
                    "summary": {"result": expected_results[label]},
                }

            with mock.patch.object(module, "run_json_contract", side_effect=fake_run_json_contract):
                summary = module.run_preclaim_contracts(args)
            self.assertTrue((Path(summary["work_dir"]) / module.WORK_DIR_MARKER).is_file())

        self.assertEqual(summary["result"], "adapter_contract_preclaim_passed")
        self.assertEqual(summary["binding_command_mode"], "default_generic_shell")
        self.assertIn("without claiming Claude, Gemini, or other host support", summary["contract_note"])
        self.assertEqual(
            [contract["label"] for contract in summary["contracts"]],
            [
                "host_signal_schema",
                "generic_host_signal_smoke",
                "generic_shell_replay",
                "generic_host_shell_parity",
                "external_host_binding_conformance",
            ],
        )
        self.assertEqual(
            [contract["result"] for contract in summary["contracts"]],
            [
                "host_signal_contract_schema_passed",
                "generic_host_signal_smoke_passed",
                "generic_shell_host_binding_replay_contract_passed",
                "generic_host_signal_shell_parity_contract_passed",
                "external_host_binding_conformance_passed",
            ],
        )
        self.assertEqual(
            [label for label, _command in calls],
            [
                "host_signal_schema",
                "generic_host_signal_smoke",
                "generic_shell_replay",
                "generic_host_shell_parity",
                "external_host_binding_conformance",
            ],
        )
        self.assertTrue(any("host_signal_contract.py" in " ".join(command) for _label, command in calls))
        self.assertTrue(any("--replay-contract" in command for _label, command in calls))
        self.assertTrue(any("--parity-contract" in command for _label, command in calls))

    def test_adapter_contract_runner_forwards_binding_command_template(self):
        module = load_runner_module()
        template = (
            f'"{portable_path(sys.executable)}" "{portable_path(SHELL_BINDING_SCRIPT)}" '
            '--host-event-file "{host_event_file}" '
            '--work-dir "{case_work_dir}" '
            f'--cadence-python "{portable_path(sys.executable)}"'
        )
        with tempfile.TemporaryDirectory() as tmp:
            args = module.build_parser().parse_args(
                [
                    "--work-dir",
                    str(Path(tmp) / "work"),
                    "--binding-command-template",
                    template,
                    "--cadence-python",
                    sys.executable,
                ]
            )
            calls = []

            def fake_run_json_contract(label, command):
                calls.append((label, command))
                return {
                    "label": label,
                    "command": command,
                    "result": f"{label}_passed",
                    "summary": {"result": f"{label}_passed", "binding_command_mode": "template"},
                }

            with mock.patch.object(module, "run_json_contract", side_effect=fake_run_json_contract):
                summary = module.run_preclaim_contracts(args)

        self.assertEqual(summary["binding_command_mode"], "template")
        conformance = next(
            contract for contract in summary["contracts"] if contract["label"] == "external_host_binding_conformance"
        )
        self.assertEqual(conformance["summary"]["binding_command_mode"], "template")
        external_command = next(command for label, command in calls if label == "external_host_binding_conformance")
        self.assertIn("--binding-command-template", external_command)
        self.assertEqual(external_command[external_command.index("--binding-command-template") + 1], template)

    def test_adapter_contract_runner_reports_child_failure(self):
        module = load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            args = module.build_parser().parse_args(["--work-dir", str(Path(tmp) / "work")])
            with mock.patch.object(
                module,
                "contract_commands",
                return_value=[
                    ("bad_contract", [sys.executable, "-c", "import sys; print('bad out'); sys.exit(7)"])
                ],
            ):
                with self.assertRaisesRegex(RuntimeError, "bad_contract failed with 7"):
                    module.run_preclaim_contracts(args)

    def test_adapter_contract_runner_reports_malformed_child_json(self):
        module = load_runner_module()
        with mock.patch.object(
            module,
            "run",
            return_value=subprocess.CompletedProcess(["child"], 0, stdout="not-json", stderr=""),
        ):
            with self.assertRaisesRegex(RuntimeError, "bad_json did not emit JSON"):
                module.run_json_contract("bad_json", ["child"])

        with mock.patch.object(
            module,
            "run",
            return_value=subprocess.CompletedProcess(["child"], 0, stdout="[]", stderr=""),
        ):
            with self.assertRaisesRegex(RuntimeError, "array_json emitted JSON list, expected object"):
                module.run_json_contract("array_json", ["child"])

    def test_adapter_contract_runner_is_documented_and_in_ci(self):
        paths = [
            ROOT / "README.md",
            ROOT / "docs" / "adapters.md",
            ROOT / "docs" / "roadmap.md",
            ROOT / "examples" / "adapter-template" / "host-binding-mapping.md",
        ]
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn("examples/adapter-contract-runner/run.py", path.read_text(encoding="utf-8"))

        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")
        self.assertIn("Run generic adapter contract pre-claim suite", workflow)
        self.assertIn("examples/adapter-contract-runner/run.py --cadence-python python", workflow)
        self.assertEqual(workflow.count("Run generic adapter contract pre-claim suite"), 1)
        self.assertLess(
            workflow.index("Run generic adapter contract pre-claim suite"),
            workflow.index("  package:"),
        )

        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("examples/adapter-contract-runner/work/", ignore_text)


if __name__ == "__main__":
    unittest.main()
