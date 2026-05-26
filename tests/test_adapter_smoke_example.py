import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "examples" / "adapter-smoke" / "run.py"


def load_smoke_module():
    spec = importlib.util.spec_from_file_location("adapter_smoke_run", SMOKE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AdapterSmokeExampleTests(unittest.TestCase):
    def test_adapter_smoke_example_uses_public_cli_only(self):
        self.assertTrue(SMOKE_SCRIPT.exists(), "missing adapter smoke example")
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
        self.assertIn("AGENTIC_CADENCE_PYTHON", source)
        self.assertNotIn("CODEX_CADENCE_PYTHON", source)
        self.assertNotIn("Codex Review", source)

        adapters = (ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("examples/adapter-smoke/run.py", adapters)
        self.assertIn("Codex-compatible packet labels", adapters)
        self.assertIn("Codex-compatible packet labels", readme)

    def test_adapter_smoke_example_runs_cli_json_contract(self):
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
            self.assertEqual(summary["result"], "adapter_smoke_passed")
            self.assertEqual(
                summary["command_sequence"],
                [
                    "status",
                    "prepare-handoff",
                    "claim-handoff",
                    "approve-handoff",
                    "claim-handoff",
                    "complete-handoff",
                    "discover-candidates",
                    "pr-body-preflight",
                    "pr-readiness",
                ],
            )
            trace = summary["command_trace"]
            self.assertEqual([entry["command"] for entry in trace], summary["command_sequence"])
            old_session_commands = [entry["command"] for entry in trace if entry["phase"] == "old_session_adapter"]
            self.assertEqual(old_session_commands, ["status", "prepare-handoff"])
            prepare_trace = next(entry for entry in trace if entry["command"] == "prepare-handoff")
            self.assertTrue(prepare_trace["stops_current_session"])
            after_prepare = trace[trace.index(prepare_trace) + 1 :]
            self.assertTrue(after_prepare)
            self.assertNotIn("old_session_adapter", {entry["phase"] for entry in after_prepare})

            self.assertEqual(summary["packets"]["status"]["cadence"]["state"], "PLAY_ON")
            prepare = summary["packets"]["prepare_handoff"]
            self.assertTrue(prepare["stop_current_session"])
            for key in ("handoff", "snapshot", "validation", "clean_square"):
                self.assertIn(key, prepare)
            self.assertEqual(prepare["handoff"]["status"], "READY")
            self.assertTrue(prepare["validation"]["valid"])
            self.assertTrue(prepare["clean_square"]["checks"]["handoff_written"])

            blocked = summary["packets"]["claim_before_approval"]
            self.assertTrue(blocked["blocked_by_policy"])
            self.assertTrue(blocked["blocked_by_policy"]["pickup_requires_approval"])
            self.assertEqual(summary["packets"]["approve_handoff"]["handoff_id"], "adapter-gate")
            self.assertEqual(summary["packets"]["claim_after_approval"]["id"], "adapter-gate")
            self.assertEqual(summary["packets"]["complete_handoff"]["status"], "COMPLETED")
            self.assertGreaterEqual(summary["packets"]["discover_candidates"]["sources"]["text_markers"], 1)
            self.assertTrue(summary["packets"]["pr_body_preflight"]["ready_to_publish"])
            self.assertTrue(summary["packets"]["pr_readiness"]["ready_to_merge"])

    def test_adapter_smoke_example_ignores_global_git_signing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            global_config = Path(tmp) / "global-gitconfig"
            global_config.write_text("[commit]\n\tgpgSign = true\n", encoding="utf-8")
            env = os.environ.copy()
            env["GIT_CONFIG_GLOBAL"] = str(global_config)

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
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["result"], "adapter_smoke_passed")

    def test_adapter_smoke_replace_existing_removes_readonly_git_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "work"
            object_dir = work_dir / "repo" / ".git" / "objects" / "aa"
            object_dir.mkdir(parents=True)
            readonly_object = object_dir / "fixture"
            readonly_object.write_text("readonly\n", encoding="utf-8")
            readonly_object.chmod(0o444)
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SMOKE_SCRIPT),
                        "--work-dir",
                        str(work_dir),
                        "--replace-existing",
                        "--cadence-python",
                        sys.executable,
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
            finally:
                if readonly_object.exists():
                    readonly_object.chmod(0o666)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["result"], "adapter_smoke_passed")

    def test_adapter_smoke_non_json_error_reports_actual_exit_code(self):
        smoke = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            command = Path(tmp) / "bad_json.py"
            command.write_text("import sys\nprint('not-json')\nsys.exit(3)\n", encoding="utf-8")

            with self.assertRaises(RuntimeError) as error:
                smoke.run_cadence(
                    [sys.executable, str(command)],
                    [],
                    [],
                    ["claim-handoff"],
                    phase="new_session_adapter_before_approval",
                    actor="new_session_adapter",
                    expect=3,
                )

            self.assertIn("exit 3", str(error.exception))

    def test_adapter_smoke_example_runs_in_package_matrix(self):
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")

        self.assertIn("Run adapter smoke example", workflow)
        self.assertIn("python examples/adapter-smoke/run.py --cadence-python python", workflow)
        self.assertIn("Package install and first-run examples", workflow)


if __name__ == "__main__":
    unittest.main()
