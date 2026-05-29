import ast
import contextlib
import io
import importlib.util
import json
import re
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


def compact_evidence_fixture(module):
    expected_results = {
        "host_signal_schema": "host_signal_contract_schema_passed",
        "generic_host_signal_smoke": "generic_host_signal_smoke_passed",
        "generic_shell_replay": "generic_shell_host_binding_replay_contract_passed",
        "generic_host_shell_parity": "generic_host_signal_shell_parity_contract_passed",
        "external_host_binding_conformance": "external_host_binding_conformance_passed",
    }
    return module.compact_evidence_summary(
        {
            "result": "adapter_contract_preclaim_passed",
            "work_dir": "work",
            "binding_command_mode": "default_generic_shell",
            "binding_command_template": None,
            "contract_note": "generic only; no named host support claim",
            "contracts": [
                {
                    "label": label,
                    "command": ["python", f"examples/{label}/run.py"],
                    "result": result,
                    "summary": {"result": result, "packets": {"omitted": True}},
                }
                for label, result in expected_results.items()
            ],
        }
    )


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

    def test_adapter_contract_runner_builds_compact_evidence_summary(self):
        module = load_runner_module()
        template = (
            'python path/to/external-binding.py --host-event-file "{host_event_file}" '
            '--work-dir "{case_work_dir}" {cadence_args}'
        )
        expected_results = {
            "host_signal_schema": "host_signal_contract_schema_passed",
            "generic_host_signal_smoke": "generic_host_signal_smoke_passed",
            "generic_shell_replay": "generic_shell_host_binding_replay_contract_passed",
            "generic_host_shell_parity": "generic_host_signal_shell_parity_contract_passed",
            "external_host_binding_conformance": "external_host_binding_conformance_passed",
        }
        full_summary = {
            "result": "adapter_contract_preclaim_passed",
            "work_dir": "work",
            "binding_command_mode": "template",
            "binding_command_template": template,
            "contract_note": "generic only; no named host support claim",
            "contracts": [
                {
                    "label": label,
                    "command": ["python", f"examples/{label}/run.py"],
                    "result": result,
                    "summary": {"result": result, "large": {"packet": "omitted"}},
                }
                for label, result in expected_results.items()
            ],
        }

        evidence = module.compact_evidence_summary(full_summary)

        self.assertEqual(evidence["schema_version"], "generic-adapter-contract-evidence.v1")
        self.assertEqual(
            set(evidence),
            {
                "schema_version",
                "result",
                "evidence_mode",
                "binding_command_mode",
                "binding_command_template",
                "contract_note",
                "contracts",
                "checklist_evidence",
            },
        )
        self.assertEqual(evidence["result"], "adapter_contract_preclaim_passed")
        self.assertEqual(evidence["evidence_mode"], "compact")
        self.assertEqual(evidence["binding_command_mode"], "template")
        self.assertEqual(evidence["binding_command_template"], template)
        self.assertEqual(
            evidence["contracts"],
            [{"label": label, "result": result} for label, result in expected_results.items()],
        )
        self.assertTrue(evidence["checklist_evidence"]["all_contracts_passed"])
        self.assertTrue(evidence["checklist_evidence"]["all_required_contracts_observed"])
        self.assertEqual(
            set(evidence["checklist_evidence"]),
            {
                "generic_only",
                "mapping_evidence_path",
                "required_contract_labels",
                "observed_contract_labels",
                "all_required_contracts_observed",
                "all_contracts_passed",
                "binding_template_placeholders",
            },
        )
        self.assertEqual(evidence["checklist_evidence"]["required_contract_labels"], list(expected_results))
        self.assertEqual(evidence["checklist_evidence"]["observed_contract_labels"], list(expected_results))
        self.assertEqual(
            evidence["checklist_evidence"]["binding_template_placeholders"],
            {
                "host_event_file": True,
                "case_work_dir": True,
                "cadence_args": True,
            },
        )
        self.assertEqual(
            evidence["checklist_evidence"]["mapping_evidence_path"],
            "examples/adapter-template/host-binding-mapping.md",
        )
        self.assertIn("generic_only", evidence["checklist_evidence"])
        for contract in evidence["contracts"]:
            self.assertNotIn("command", contract)
            self.assertNotIn("summary", contract)

    def test_adapter_contract_runner_evidence_summary_flags_missing_contracts(self):
        module = load_runner_module()
        full_summary = {
            "result": "adapter_contract_preclaim_passed",
            "binding_command_mode": "default_generic_shell",
            "binding_command_template": None,
            "contract_note": "generic only; no named host support claim",
            "contracts": [
                {
                    "label": "host_signal_schema",
                    "result": "host_signal_contract_schema_passed",
                    "summary": {"result": "host_signal_contract_schema_passed"},
                },
            ],
        }

        evidence = module.compact_evidence_summary(full_summary)

        self.assertFalse(evidence["checklist_evidence"]["all_required_contracts_observed"])
        self.assertFalse(evidence["checklist_evidence"]["all_contracts_passed"])
        self.assertEqual(evidence["checklist_evidence"]["observed_contract_labels"], ["host_signal_schema"])
        errors = module.validate_compact_evidence_against_schema(evidence)
        self.assertIn("contracts labels must exactly match required_contract_labels", errors)
        self.assertIn("checklist_evidence.all_required_contracts_observed must be true", errors)
        self.assertIn("checklist_evidence.all_contracts_passed must be true", errors)

    def test_adapter_contract_runner_evidence_summary_parses_format_placeholders(self):
        module = load_runner_module()
        full_summary = {
            "result": "adapter_contract_preclaim_passed",
            "binding_command_mode": "template",
            "binding_command_template": (
                "python binding.py --literal {{host_event_file}} "
                "--work-dir {case_work_dir!s} {cadence_args:required}"
            ),
            "contract_note": "generic only; no named host support claim",
            "contracts": [],
        }

        evidence = module.compact_evidence_summary(full_summary)

        self.assertEqual(
            evidence["checklist_evidence"]["binding_template_placeholders"],
            {
                "host_event_file": False,
                "case_work_dir": True,
                "cadence_args": True,
            },
        )

    def test_adapter_contract_runner_validates_compact_evidence_against_checked_in_schema(self):
        module = load_runner_module()
        expected_results = {
            "host_signal_schema": "host_signal_contract_schema_passed",
            "generic_host_signal_smoke": "generic_host_signal_smoke_passed",
            "generic_shell_replay": "generic_shell_host_binding_replay_contract_passed",
            "generic_host_shell_parity": "generic_host_signal_shell_parity_contract_passed",
            "external_host_binding_conformance": "external_host_binding_conformance_passed",
        }
        full_summary = {
            "result": "adapter_contract_preclaim_passed",
            "work_dir": "work",
            "binding_command_mode": "default_generic_shell",
            "binding_command_template": None,
            "contract_note": "generic only; no named host support claim",
            "contracts": [
                {
                    "label": label,
                    "command": ["python", f"examples/{label}/run.py"],
                    "result": result,
                    "summary": {"result": result, "packets": {"omitted": True}},
                }
                for label, result in expected_results.items()
            ],
        }
        evidence = module.compact_evidence_summary(full_summary)
        schema_path = ROOT / module.EVIDENCE_SCHEMA_PATH

        self.assertTrue(schema_path.is_file())
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["schema_version"], module.EVIDENCE_SCHEMA_VERSION)
        self.assertEqual(schema["artifact_name"], "generic-adapter-contract-evidence")
        self.assertEqual(schema["artifact_file"], "adapter-contract-evidence.json")
        self.assertEqual(schema["result"], "adapter_contract_preclaim_passed")
        self.assertEqual(schema["top_level_keys"], sorted(evidence))
        self.assertEqual(schema["checklist_evidence_keys"], sorted(evidence["checklist_evidence"]))
        self.assertEqual(schema["contract_entry_keys"], ["label", "result"])
        self.assertEqual(schema["required_contract_labels"], module.REQUIRED_CONTRACT_LABELS)
        self.assertTrue(schema["contract_labels_must_match_required"])
        self.assertTrue(schema["observed_contract_labels_must_match_contracts"])
        self.assertEqual(
            schema["checklist_required_true_keys"],
            ["all_required_contracts_observed", "all_contracts_passed"],
        )
        self.assertEqual(module.validate_compact_evidence_against_schema(evidence), [])

        missing_schema = dict(evidence)
        missing_schema.pop("schema_version")
        self.assertIn("missing top-level key: schema_version", module.validate_compact_evidence_against_schema(missing_schema))

        nested_packet = dict(evidence)
        nested_packet["contracts"] = [dict(evidence["contracts"][0], summary={"packets": {"large": True}})]
        self.assertIn(
            "contracts[0] keys must be ['label', 'result']",
            module.validate_compact_evidence_against_schema(nested_packet),
        )
        duplicate_contract = json.loads(json.dumps(evidence))
        duplicate_contract["contracts"][1]["label"] = "host_signal_schema"
        self.assertIn(
            "contracts labels must exactly match required_contract_labels",
            module.validate_compact_evidence_against_schema(duplicate_contract),
        )
        observed_mismatch = json.loads(json.dumps(evidence))
        observed_mismatch["checklist_evidence"]["observed_contract_labels"] = list(reversed(schema["required_contract_labels"]))
        self.assertIn(
            "checklist_evidence.observed_contract_labels must match contracts labels",
            module.validate_compact_evidence_against_schema(observed_mismatch),
        )
        failed_result = json.loads(json.dumps(evidence))
        failed_result["result"] = "adapter_contract_preclaim_failed"
        self.assertIn(
            "result must be adapter_contract_preclaim_passed",
            module.validate_compact_evidence_against_schema(failed_result),
        )

    def test_adapter_contract_runner_evidence_summary_cli_omits_nested_packets(self):
        module = load_runner_module()
        full_summary = {
            "result": "adapter_contract_preclaim_passed",
            "work_dir": "work",
            "binding_command_mode": "default_generic_shell",
            "binding_command_template": None,
            "contract_note": "generic only; no named host support claim",
            "contracts": [
                {
                    "label": label,
                    "command": ["python", f"examples/{label}/run.py"],
                    "result": result,
                    "summary": {"result": result, "packets": {"large": True}},
                }
                for label, result in {
                    "host_signal_schema": "host_signal_contract_schema_passed",
                    "generic_host_signal_smoke": "generic_host_signal_smoke_passed",
                    "generic_shell_replay": "generic_shell_host_binding_replay_contract_passed",
                    "generic_host_shell_parity": "generic_host_signal_shell_parity_contract_passed",
                    "external_host_binding_conformance": "external_host_binding_conformance_passed",
                }.items()
            ],
        }

        with mock.patch.object(module, "run_preclaim_contracts", return_value=full_summary):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = module.main(["--evidence-summary"])

        self.assertEqual(exit_code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["evidence_mode"], "compact")
        self.assertEqual(output["schema_version"], "generic-adapter-contract-evidence.v1")
        self.assertEqual(
            [contract["label"] for contract in output["contracts"]],
            [
                "host_signal_schema",
                "generic_host_signal_smoke",
                "generic_shell_replay",
                "generic_host_shell_parity",
                "external_host_binding_conformance",
            ],
        )
        self.assertEqual({frozenset(contract) for contract in output["contracts"]}, {frozenset({"label", "result"})})
        serialized = json.dumps(output)
        self.assertNotIn("packets", serialized)
        self.assertNotIn('"command": [', serialized)

    def test_adapter_contract_runner_validates_evidence_file_without_rerunning_contracts(self):
        module = load_runner_module()
        evidence = compact_evidence_fixture(module)
        with tempfile.TemporaryDirectory() as tmp:
            evidence_file = Path(tmp) / "adapter-contract-evidence.json"
            evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

            stdout = io.StringIO()
            with mock.patch.object(module, "run_preclaim_contracts") as run_contracts:
                with contextlib.redirect_stdout(stdout):
                    exit_code = module.main(["--validate-evidence-file", str(evidence_file)])

        self.assertEqual(exit_code, 0)
        run_contracts.assert_not_called()
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["result"], "adapter_contract_evidence_validation_passed")
        self.assertEqual(summary["schema_version"], "generic-adapter-contract-evidence.v1")
        self.assertEqual(summary["contract_count"], 5)
        self.assertEqual(summary["evidence_file"], str(evidence_file))

    def test_adapter_contract_runner_validates_utf16_evidence_file_from_powershell_redirection(self):
        module = load_runner_module()
        evidence = compact_evidence_fixture(module)
        with tempfile.TemporaryDirectory() as tmp:
            evidence_file = Path(tmp) / "adapter-contract-evidence.json"
            evidence_file.write_text(json.dumps(evidence), encoding="utf-16")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = module.main(["--validate-evidence-file", str(evidence_file)])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(stdout.getvalue())["result"],
            "adapter_contract_evidence_validation_passed",
        )

    def test_adapter_contract_runner_rejects_malformed_evidence_file_json(self):
        module = load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            evidence_file = Path(tmp) / "adapter-contract-evidence.json"
            evidence_file.write_text("{not-json", encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = module.main(["--validate-evidence-file", str(evidence_file)])

        self.assertEqual(exit_code, 1)
        self.assertIn("adapter contract evidence validation failed:", stderr.getvalue())
        self.assertIn("not valid JSON", stderr.getvalue())

    def test_adapter_contract_runner_rejects_wrong_evidence_file_schema_version(self):
        module = load_runner_module()
        evidence = compact_evidence_fixture(module)
        evidence["schema_version"] = "generic-adapter-contract-evidence.v0"
        with tempfile.TemporaryDirectory() as tmp:
            evidence_file = Path(tmp) / "adapter-contract-evidence.json"
            evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = module.main(["--validate-evidence-file", str(evidence_file)])

        self.assertEqual(exit_code, 1)
        self.assertIn("schema_version must be generic-adapter-contract-evidence.v1", stderr.getvalue())

    def test_adapter_contract_runner_rejects_evidence_file_failed_result(self):
        module = load_runner_module()
        evidence = compact_evidence_fixture(module)
        evidence["result"] = "adapter_contract_preclaim_failed"
        with tempfile.TemporaryDirectory() as tmp:
            evidence_file = Path(tmp) / "adapter-contract-evidence.json"
            evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = module.main(["--validate-evidence-file", str(evidence_file)])

        self.assertEqual(exit_code, 1)
        self.assertIn("result must be adapter_contract_preclaim_passed", stderr.getvalue())

    def test_adapter_contract_runner_rejects_evidence_file_missing_contracts(self):
        module = load_runner_module()
        evidence = compact_evidence_fixture(module)
        evidence["contracts"] = evidence["contracts"][:-1]
        evidence["checklist_evidence"]["observed_contract_labels"] = [
            contract["label"] for contract in evidence["contracts"]
        ]
        with tempfile.TemporaryDirectory() as tmp:
            evidence_file = Path(tmp) / "adapter-contract-evidence.json"
            evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = module.main(["--validate-evidence-file", str(evidence_file)])

        self.assertEqual(exit_code, 1)
        self.assertIn("contracts labels must exactly match required_contract_labels", stderr.getvalue())

    def test_adapter_contract_runner_rejects_evidence_file_failing_contract_booleans(self):
        module = load_runner_module()
        evidence = compact_evidence_fixture(module)
        evidence["checklist_evidence"]["all_contracts_passed"] = False
        with tempfile.TemporaryDirectory() as tmp:
            evidence_file = Path(tmp) / "adapter-contract-evidence.json"
            evidence_file.write_text(json.dumps(evidence), encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = module.main(["--validate-evidence-file", str(evidence_file)])

        self.assertEqual(exit_code, 1)
        self.assertIn("checklist_evidence.all_contracts_passed must be true", stderr.getvalue())

    def test_adapter_contract_runner_rejects_conflicting_cadence_flags(self):
        module = load_runner_module()
        parser = module.build_parser()

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--cadence-command", "agentic-cadence", "--cadence-python", sys.executable])

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

        checklist = (ROOT / "docs" / "adapter-claim-checklist.md").read_text(encoding="utf-8")
        self.assertIn("examples/adapter-contract-runner/run.py --cadence-python python --evidence-summary", checklist)
        binding_evidence_command = re.search(
            r"python\s+examples/adapter-contract-runner/run\.py\b"
            r"(?:(?!```).)*--binding-command-template"
            r"(?:(?!```).)*--evidence-summary",
            checklist,
            re.S,
        )
        self.assertIsNotNone(binding_evidence_command)
        for token in (
            "--binding-command-template",
            "--evidence-summary",
            "{host_event_file}",
            "{case_work_dir}",
            "{cadence_args}",
        ):
            with self.subTest(token=token):
                self.assertIn(token, binding_evidence_command.group(0))
        self.assertIn("compact evidence summary", checklist)

    def test_pr_workflow_uploads_compact_adapter_evidence_artifact(self):
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")
        runner_step_name = "Run generic adapter contract pre-claim suite"
        validate_step_name = "Validate generic adapter contract evidence"
        upload_step_name = "Upload generic adapter contract evidence"
        run_step = (
            "python examples/adapter-contract-runner/run.py --cadence-python python "
            "--evidence-summary | tee adapter-contract-evidence.json"
        )
        validate_step = (
            "python examples/adapter-contract-runner/run.py "
            "--validate-evidence-file adapter-contract-evidence.json"
        )
        self.assertIn(validate_step_name, workflow)
        runner_step = workflow[workflow.index(runner_step_name) : workflow.index(validate_step_name)]
        validation_step = workflow[workflow.index(validate_step_name) : workflow.index(upload_step_name)]
        upload_step = workflow[workflow.index(upload_step_name) : workflow.index("  package:")]

        for token in (
            run_step,
            "shell: bash",
        ):
            with self.subTest(runner_step_token=token):
                self.assertIn(token, runner_step)

        for token in (
            validate_step,
            "shell: bash",
        ):
            with self.subTest(validation_step_token=token):
                self.assertIn(token, validation_step)

        for token in (
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "name: generic-adapter-contract-evidence",
            "path: adapter-contract-evidence.json",
            "if-no-files-found: error",
        ):
            with self.subTest(upload_step_token=token):
                self.assertIn(token, upload_step)

        self.assertLess(workflow.index(run_step), workflow.index(upload_step_name))
        self.assertLess(workflow.index(run_step), workflow.index(validate_step_name))
        self.assertLess(workflow.index(validate_step), workflow.index(upload_step_name))
        self.assertLess(workflow.index(upload_step_name), workflow.index("  package:"))

        canonical_docs = (
            ROOT / "README.md",
            ROOT / "docs" / "adapter-claim-checklist.md",
            ROOT / "docs" / "adapters.md",
        )
        schema_path = "examples/adapter-contract-runner/generic-adapter-contract-evidence.v1.schema.json"
        for path in canonical_docs:
            text = path.read_text(encoding="utf-8")
            with self.subTest(canonical_evidence_doc=path.relative_to(ROOT)):
                self.assertIn("generic-adapter-contract-evidence", text)
                self.assertIn("adapter-contract-evidence.json", text)
                self.assertIn("generic-adapter-contract-evidence.v1", text)
                self.assertIn(schema_path, text)
                self.assertIn(
                    "python examples/adapter-contract-runner/run.py --validate-evidence-file adapter-contract-evidence.json",
                    text,
                )

    def test_adapter_evidence_current_state_docs_point_to_canonical_checklist(self):
        pointer_docs = (
            ROOT / "docs" / "roadmap.md",
            ROOT / "examples" / "adapter-template" / "README.md",
            ROOT / "examples" / "adapter-template" / "host-binding-mapping.md",
        )
        for path in pointer_docs:
            text = path.read_text(encoding="utf-8")
            with self.subTest(pointer_doc=path.relative_to(ROOT)):
                self.assertIn("generic-adapter-contract-evidence", text)
                self.assertIn("adapter-contract-evidence.json", text)
                self.assertIn("docs/adapter-claim-checklist.md", text)


if __name__ == "__main__":
    unittest.main()
