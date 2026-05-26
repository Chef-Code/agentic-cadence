import ast
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_SCRIPT = ROOT / "examples" / "adapter-template" / "adapter.py"
TEMPLATE_README = ROOT / "examples" / "adapter-template" / "README.md"


def load_template_module():
    spec = importlib.util.spec_from_file_location("adapter_template", TEMPLATE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AdapterTemplateExampleTests(unittest.TestCase):
    def test_adapter_template_uses_public_cli_only(self):
        self.assertTrue(TEMPLATE_SCRIPT.exists(), "missing adapter template")
        self.assertTrue(TEMPLATE_README.exists(), "missing adapter template README")
        source = TEMPLATE_SCRIPT.read_text(encoding="utf-8")
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
        self.assertIn("detect_context_pressure", source)
        self.assertIn("render_pickup_text", source)

    def test_adapter_template_preserves_packets_and_stops_after_prepare(self):
        template = load_template_module()
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "runtime"
            cwd = Path(tmp) / "repo"
            cwd.mkdir()

            packets = {
                "status": {"cadence": {"state": "PLAY_ON"}},
                "prepare-handoff": {
                    "stop_current_session": True,
                    "handoff": {"id": "template-handoff", "message": "raw packet body"},
                    "validation": {"valid": True},
                },
            }
            calls = []

            def fake_runner(command, *, runtime_root, cadence_command):
                calls.append((command, runtime_root, cadence_command))
                return packets[command[0]]

            result = template.prepare_context_handoff(
                runtime_root=runtime_root,
                repo="local/template",
                cwd=cwd,
                handoff_id="template-handoff",
                title="Template handoff",
                summary="template summary",
                next_action="start from preserved packet",
                cadence_command=["agentic-cadence"],
                runner=fake_runner,
                context_pressure_detector=lambda: True,
            )

            self.assertIs(result["packets"]["status"], packets["status"])
            self.assertIs(result["packets"]["prepare_handoff"], packets["prepare-handoff"])
            self.assertEqual([call[0][0] for call in calls], ["status", "prepare-handoff"])
            self.assertTrue(result["stop_current_session"])
            self.assertIn("template-handoff", result["pickup_text"])
            self.assertNotIn("raw packet body", result["pickup_text"])
            self.assertIn("preserved Cadence JSON packet", result["pickup_text"])

            for _, root_arg, _ in calls:
                self.assertEqual(root_arg, runtime_root)

    def test_adapter_template_passes_task_sizing_inputs(self):
        template = load_template_module()
        calls = []

        def fake_runner(command, *, runtime_root, cadence_command):
            calls.append(command)
            if command[0] == "status":
                return {"cadence": {"state": "PLAY_ON"}}
            return {
                "stop_current_session": True,
                "handoff": {
                    "id": "template-handoff",
                    "status": "READY",
                    "estimate": {
                        "task_type": "discovery",
                        "drivers": ["unknown_repo_area", "cross_subsystem", "unclear_requirements"],
                        "policy": {"pickup_requires_approval": True},
                    },
                },
            }

        result = template.prepare_context_handoff(
            runtime_root=Path("runtime"),
            repo="local/template",
            cwd=Path("."),
            handoff_id="template-handoff",
            title="Template handoff",
            summary="template summary",
            next_action="start from preserved packet",
            cadence_command=["agentic-cadence"],
            task_type="discovery",
            drivers=["unknown_repo_area", "cross_subsystem", "unclear_requirements"],
            runner=fake_runner,
            context_pressure_detector=lambda: True,
        )

        prepare_command = calls[1]
        self.assertIn("--task-type", prepare_command)
        self.assertEqual(prepare_command[prepare_command.index("--task-type") + 1], "discovery")
        self.assertEqual(
            [prepare_command[index + 1] for index, value in enumerate(prepare_command) if value == "--driver"],
            ["unknown_repo_area", "cross_subsystem", "unclear_requirements"],
        )
        self.assertTrue(
            result["packets"]["prepare_handoff"]["handoff"]["estimate"]["policy"]["pickup_requires_approval"]
        )

    def test_adapter_template_requires_explicit_runtime_root_and_play_on(self):
        template = load_template_module()
        parser = template.build_parser()
        runtime_action = next(action for action in parser._actions if "--runtime-root" in action.option_strings)
        self.assertTrue(runtime_action.required)

        with self.assertRaisesRegex(RuntimeError, "Cadence state is HUDDLE"):
            template.prepare_context_handoff(
                runtime_root=Path("runtime"),
                repo="local/template",
                cwd=Path("."),
                handoff_id="template-handoff",
                title="Template handoff",
                summary="template summary",
                next_action="start from preserved packet",
                cadence_command=["agentic-cadence"],
                runner=lambda command, **_: {"cadence": {"state": "HUDDLE"}},
                context_pressure_detector=lambda: True,
            )

    def test_adapter_template_splits_windows_cadence_command(self):
        template = load_template_module()
        self.assertEqual(
            template.split_cadence_command(r"C:\Python312\python.exe -m codex_cadence", windows=True),
            [r"C:\Python312\python.exe", "-m", "codex_cadence"],
        )
        self.assertEqual(
            template.split_cadence_command(r'"C:\Program Files\Python312\python.exe" -m codex_cadence', windows=True),
            [r"C:\Program Files\Python312\python.exe", "-m", "codex_cadence"],
        )

    def test_adapter_template_is_documented_from_adapter_docs_and_roadmap(self):
        adapters = (ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        readme = TEMPLATE_README.read_text(encoding="utf-8")

        for text in (adapters, roadmap, readme):
            with self.subTest(document=text[:40]):
                self.assertIn("examples/adapter-template", text)

        for token in (
            "public CLI",
            "explicit runtime root",
            "preserve returned JSON packets",
            "stop_current_session",
            "does not ship a Claude or Gemini adapter",
        ):
            with self.subTest(token=token):
                self.assertIn(token, readme)


if __name__ == "__main__":
    unittest.main()
