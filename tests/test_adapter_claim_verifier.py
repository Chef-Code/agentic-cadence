import ast
import contextlib
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER_SCRIPT = ROOT / "examples" / "adapter-contract-runner" / "run.py"
VERIFIER_SCRIPT = ROOT / "examples" / "adapter-claim-verifier" / "run.py"


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_runner_module():
    return load_module(RUNNER_SCRIPT, "adapter_contract_runner_for_claim_verifier_tests")


def load_verifier_module():
    return load_module(VERIFIER_SCRIPT, "adapter_claim_verifier")


def compact_evidence_fixture(*, binding_template: str | None = None) -> dict:
    runner = load_runner_module()
    expected_results = {
        "host_signal_schema": "host_signal_contract_schema_passed",
        "generic_host_signal_smoke": "generic_host_signal_smoke_passed",
        "generic_shell_replay": "generic_shell_host_binding_replay_contract_passed",
        "generic_host_shell_parity": "generic_host_signal_shell_parity_contract_passed",
        "external_host_binding_conformance": "external_host_binding_conformance_passed",
    }
    return runner.compact_evidence_summary(
        {
            "result": "adapter_contract_preclaim_passed",
            "work_dir": "work",
            "binding_command_mode": "template" if binding_template else "default_generic_shell",
            "binding_command_template": binding_template,
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


class AdapterClaimVerifierTests(unittest.TestCase):
    def test_adapter_claim_verifier_uses_public_evidence_boundary_only(self):
        self.assertTrue(VERIFIER_SCRIPT.exists(), "missing adapter claim verifier")
        source = VERIFIER_SCRIPT.read_text(encoding="utf-8")
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
        self.assertIn("adapter-contract-runner", source)
        self.assertIn("does not implement or claim support", source)
        runner = load_runner_module()
        verifier = load_verifier_module()
        self.assertEqual(
            verifier.required_template_placeholders(runner),
            tuple(runner.load_evidence_schema()["binding_template_placeholder_keys"]),
        )
        self.assertNotIn("REQUIRED_TEMPLATE_PLACEHOLDERS", source)

    def run_verifier(self, args: list[str]) -> tuple[int, dict, str]:
        verifier = load_verifier_module()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = verifier.main(args)
        return exit_code, json.loads(stdout.getvalue()), stderr.getvalue()

    def write_evidence(self, evidence: dict) -> tuple[tempfile.TemporaryDirectory, Path]:
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "adapter-contract-evidence.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        return tmp, path

    def test_claim_verifier_keeps_generic_baseline_generic(self):
        tmp, evidence_file = self.write_evidence(compact_evidence_fixture())
        with tmp:
            exit_code, packet, stderr = self.run_verifier(["--evidence-file", str(evidence_file)])

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(packet["result"], "adapter_claim_verification_passed")
        self.assertEqual(packet["claim_decision"], "generic_only")
        self.assertFalse(packet["named_host_claim_allowed"])
        self.assertEqual(packet["recommended_next_action"], "keep_pr_generic")
        self.assertEqual(packet["binding_command_mode"], "default_generic_shell")
        self.assertEqual(packet["blockers"], [])

    def test_claim_verifier_blocks_named_claim_without_template_evidence(self):
        tmp, evidence_file = self.write_evidence(compact_evidence_fixture())
        with tmp:
            exit_code, packet, stderr = self.run_verifier(
                ["--evidence-file", str(evidence_file), "--claim-host", "ExampleHost"]
            )

        self.assertEqual(exit_code, 1, stderr)
        self.assertEqual(packet["result"], "adapter_claim_verification_failed")
        self.assertEqual(packet["claim_decision"], "must_remain_generic")
        self.assertFalse(packet["named_host_claim_allowed"])
        self.assertEqual(
            packet["recommended_next_action"],
            "keep_pr_generic_or_run_binding_contract_with_template",
        )
        self.assertIn(
            "named_claim_requires_template_binding_evidence",
            {blocker["code"] for blocker in packet["blockers"]},
        )

    def test_claim_verifier_blocks_named_claim_without_supplied_template(self):
        template = (
            'python path/to/external-binding.py --host-event-file "{host_event_file}" '
            '--work-dir "{case_work_dir}" {cadence_args}'
        )
        tmp, evidence_file = self.write_evidence(compact_evidence_fixture(binding_template=template))
        with tmp:
            exit_code, packet, stderr = self.run_verifier(
                ["--evidence-file", str(evidence_file), "--claim-host", "ExampleHost"]
            )

        self.assertEqual(exit_code, 1, stderr)
        self.assertEqual(packet["claim_decision"], "must_remain_generic")
        self.assertFalse(packet["named_host_claim_allowed"])
        self.assertIn("binding_command_template_required", {blocker["code"] for blocker in packet["blockers"]})

    def test_claim_verifier_recomputes_template_placeholders_from_evidence_template(self):
        evidence = compact_evidence_fixture(
            binding_template='python binding.py --work-dir "{case_work_dir}" {cadence_args}'
        )
        evidence["checklist_evidence"]["binding_template_placeholders"] = {
            "cadence_args": True,
            "case_work_dir": True,
            "host_event_file": True,
        }
        tmp, evidence_file = self.write_evidence(evidence)
        with tmp:
            exit_code, packet, stderr = self.run_verifier(
                [
                    "--evidence-file",
                    str(evidence_file),
                    "--claim-host",
                    "ExampleHost",
                    "--binding-command-template",
                    'python binding.py --work-dir "{case_work_dir}" {cadence_args}',
                ]
            )

        self.assertEqual(exit_code, 1, stderr)
        self.assertEqual(packet["claim_decision"], "must_remain_generic")
        self.assertFalse(packet["required_placeholders"]["host_event_file"])
        blocker_codes = {blocker["code"] for blocker in packet["blockers"]}
        self.assertIn("binding_template_placeholder_evidence_mismatch", blocker_codes)
        self.assertIn("binding_template_missing_host_event_file_placeholder", blocker_codes)

    def test_claim_verifier_reports_schema_invalid_evidence_as_json_packet(self):
        evidence = compact_evidence_fixture()
        evidence["schema_version"] = "generic-adapter-contract-evidence.v0"
        tmp, evidence_file = self.write_evidence(evidence)
        with tmp:
            exit_code, packet, stderr = self.run_verifier(["--evidence-file", str(evidence_file)])

        self.assertEqual(exit_code, 1, stderr)
        self.assertEqual(packet["result"], "adapter_claim_verification_failed")
        self.assertEqual(packet["claim_decision"], "evidence_invalid")
        self.assertEqual(packet["recommended_next_action"], "fix_adapter_contract_evidence")
        self.assertIn("invalid_adapter_contract_evidence", {blocker["code"] for blocker in packet["blockers"]})

    def test_claim_verifier_blocks_blank_claim_host(self):
        tmp, evidence_file = self.write_evidence(compact_evidence_fixture())
        with tmp:
            exit_code, packet, stderr = self.run_verifier(
                ["--evidence-file", str(evidence_file), "--claim-host", "   "]
            )

        self.assertEqual(exit_code, 1, stderr)
        self.assertEqual(packet["claim_decision"], "must_remain_generic")
        self.assertIn("claim_host_missing", {blocker["code"] for blocker in packet["blockers"]})

    def test_claim_verifier_allows_named_claim_with_template_evidence(self):
        template = (
            'python path/to/external-binding.py --host-event-file "{host_event_file}" '
            '--work-dir "{case_work_dir}" {cadence_args}'
        )
        tmp, evidence_file = self.write_evidence(compact_evidence_fixture(binding_template=template))
        with tmp:
            exit_code, packet, stderr = self.run_verifier(
                [
                    "--evidence-file",
                    str(evidence_file),
                    "--claim-host",
                    "ExampleHost",
                    "--binding-command-template",
                    template,
                ]
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(packet["result"], "adapter_claim_verification_passed")
        self.assertEqual(packet["claim_decision"], "named_host_claim_allowed")
        self.assertTrue(packet["named_host_claim_allowed"])
        self.assertTrue(packet["binding_command_template_matches_evidence"])
        self.assertEqual(packet["claim_host"], "ExampleHost")
        self.assertEqual(packet["blockers"], [])
        self.assertEqual(
            packet["required_placeholders"],
            {
                "cadence_args": True,
                "case_work_dir": True,
                "host_event_file": True,
            },
        )

    def test_claim_verifier_blocks_binding_template_mismatch(self):
        evidence_template = (
            'python path/to/external-binding.py --host-event-file "{host_event_file}" '
            '--work-dir "{case_work_dir}" {cadence_args}'
        )
        tmp, evidence_file = self.write_evidence(compact_evidence_fixture(binding_template=evidence_template))
        with tmp:
            exit_code, packet, stderr = self.run_verifier(
                [
                    "--evidence-file",
                    str(evidence_file),
                    "--claim-host",
                    "ExampleHost",
                    "--binding-command-template",
                    'python other.py --host-event-file "{host_event_file}" --work-dir "{case_work_dir}" {cadence_args}',
                ]
            )

        self.assertEqual(exit_code, 1, stderr)
        self.assertEqual(packet["claim_decision"], "must_remain_generic")
        self.assertIn("binding_command_template_mismatch", {blocker["code"] for blocker in packet["blockers"]})

    def test_adapter_claim_verifier_is_documented_and_in_ci(self):
        docs = (
            ROOT / "README.md",
            ROOT / "docs" / "adapters.md",
            ROOT / "docs" / "adapter-claim-checklist.md",
            ROOT / "docs" / "roadmap.md",
            ROOT / "examples" / "adapter-template" / "README.md",
            ROOT / "examples" / "adapter-template" / "host-binding-mapping.md",
        )
        for path in docs:
            text = path.read_text(encoding="utf-8")
            with self.subTest(doc=path.relative_to(ROOT)):
                self.assertIn("examples/adapter-claim-verifier/run.py", text)
                self.assertIn("--evidence-file adapter-contract-evidence.json", text)
                self.assertIn("--claim-host", text)

        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")
        self.assertIn("Verify generic adapter claim boundary", workflow)
        self.assertIn(
            "python examples/adapter-claim-verifier/run.py --evidence-file adapter-contract-evidence.json",
            workflow,
        )
        self.assertLess(
            workflow.index("Validate generic adapter contract evidence"),
            workflow.index("Verify generic adapter claim boundary"),
        )
        self.assertLess(
            workflow.index("Verify generic adapter claim boundary"),
            workflow.index("Upload generic adapter contract evidence"),
        )


if __name__ == "__main__":
    unittest.main()
