import ast
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
    def signal(self, template, **overrides):
        values = {
            "kind": "context_pressure",
            "source": "adapter-template-test",
            "confidence": "high",
            "summary": "signal summary",
            "task_type": "execution",
            "drivers": ("reviewer_feedback",),
            "next_action": "continue from signal",
        }
        values.update(overrides)
        return template.HostSessionSignal(**values)

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
        self.assertIn("HostSessionSignal", source)
        self.assertIn("detect_host_session_signal", source)
        self.assertIn("validate_host_session_signal", source)
        self.assertIn("render_pickup_text", source)

    def test_adapter_template_returns_without_cadence_when_signal_absent(self):
        template = load_template_module()
        calls = []

        def fake_runner(command, **_):
            calls.append(command)
            return {"unexpected": True}

        result = template.prepare_context_handoff(
            runtime_root=Path("runtime"),
            repo="local/template",
            cwd=Path("."),
            handoff_id="template-handoff",
            title="Template handoff",
            summary="unused summary",
            next_action="unused next action",
            cadence_command=["agentic-cadence"],
            task_type="execution",
            runner=fake_runner,
            host_session_signal_detector=lambda: None,
        )

        self.assertEqual(result["result"], "no_handoff_needed")
        self.assertFalse(result["stop_current_session"])
        self.assertEqual(result["packets"], {})
        self.assertEqual(calls, [])

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

            def fake_runner(command, *, runtime_root, cadence_command, **_):
                calls.append((command, runtime_root, cadence_command))
                return packets[command[0]]

            result = template.prepare_context_handoff(
                runtime_root=runtime_root,
                repo="local/template",
                cwd=cwd,
                handoff_id="template-handoff",
                title="Template handoff",
                summary="unused summary",
                next_action="unused next action",
                cadence_command=["agentic-cadence"],
                task_type="execution",
                runner=fake_runner,
                host_session_signal_detector=lambda: self.signal(
                    template,
                    summary="signal summary",
                    next_action="start from signal",
                    drivers=(),
                ),
            )

            self.assertIs(result["packets"]["status"], packets["status"])
            self.assertIs(result["packets"]["prepare_handoff"], packets["prepare-handoff"])
            self.assertEqual([call[0][0] for call in calls], ["status", "prepare-handoff"])
            prepare_command = calls[1][0]
            self.assertEqual(prepare_command[prepare_command.index("--summary") + 1], "signal summary")
            self.assertEqual(prepare_command[prepare_command.index("--next-action") + 1], "start from signal")
            self.assertNotIn("--driver", prepare_command)
            self.assertTrue(result["stop_current_session"])
            self.assertIn("template-handoff", result["pickup_text"])
            self.assertNotIn("raw packet body", result["pickup_text"])
            self.assertIn("preserved Cadence JSON packet", result["pickup_text"])

            for _, root_arg, _ in calls:
                self.assertEqual(root_arg, runtime_root)

    def test_adapter_template_passes_task_sizing_inputs(self):
        template = load_template_module()
        calls = []

        def fake_runner(command, *, runtime_root, cadence_command, **_):
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
            summary="unused summary",
            next_action="unused next action",
            cadence_command=["agentic-cadence"],
            task_type="execution",
            runner=fake_runner,
            host_session_signal_detector=lambda: self.signal(
                template,
                task_type="discovery",
                drivers=("unknown_repo_area", "cross_subsystem", "unclear_requirements"),
                summary="discovery signal",
                next_action="start from preserved packet",
            ),
        )

        prepare_command = calls[1]
        self.assertIn("--task-type", prepare_command)
        self.assertEqual(prepare_command[prepare_command.index("--task-type") + 1], "discovery")
        self.assertEqual(prepare_command[prepare_command.index("--summary") + 1], "discovery signal")
        self.assertEqual(prepare_command[prepare_command.index("--next-action") + 1], "start from preserved packet")
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
        task_type_action = next(action for action in parser._actions if "--task-type" in action.option_strings)
        self.assertTrue(runtime_action.required)
        self.assertTrue(task_type_action.required)

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
                task_type="execution",
                runner=lambda command, **_: {"cadence": {"state": "HUDDLE"}},
                host_session_signal_detector=lambda: self.signal(template),
            )

    def test_adapter_template_validates_signal_before_cadence_calls(self):
        template = load_template_module()

        cases = [
            ("kind", "ci_loop", "kind"),
            ("source", "", "source"),
            ("source", "x" * 65, "source"),
            ("confidence", "certain", "confidence"),
            ("task_type", "maintenance", "task_type"),
            ("drivers", ("typo_driver",), "driver"),
            ("drivers", "reviewer_feedback", "drivers"),
            ("summary", "   ", "summary"),
            ("next_action", "", "next_action"),
        ]

        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                calls = []

                def fake_runner(command, **_):
                    calls.append(command)
                    return {"cadence": {"state": "PLAY_ON"}}

                with self.assertRaisesRegex(RuntimeError, message):
                    template.prepare_context_handoff(
                        runtime_root=Path("runtime"),
                        repo="local/template",
                        cwd=Path("."),
                        handoff_id="template-handoff",
                        title="Template handoff",
                        summary="unused summary",
                        next_action="unused next action",
                        cadence_command=["agentic-cadence"],
                        task_type="execution",
                        runner=fake_runner,
                        host_session_signal_detector=lambda field=field, value=value: self.signal(
                            template,
                            **{field: value},
                        ),
                    )

                self.assertEqual(calls, [])

    def test_adapter_template_cadence_timeout_reports_deterministic_failure(self):
        template = load_template_module()

        def timeout_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=kwargs.get("args", args[0]), timeout=1)

        with patch.object(template.subprocess, "run", side_effect=timeout_run):
            with self.assertRaisesRegex(RuntimeError, "timed out after 1s"):
                template.run_cadence(
                    ["status"],
                    runtime_root=Path("runtime"),
                    cadence_command=["agentic-cadence"],
                    timeout_seconds=1,
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
            "detect_host_session_signal()",
            "HostSessionSignal",
            "not a stable Python API",
            "does not ship a Claude or Gemini adapter",
        ):
            with self.subTest(token=token):
                self.assertIn(token, readme)

        self.assertIn("Host/Session Signal Contract", adapters)
        self.assertIn("adapter-local `HostSessionSignal`", adapters)
        self.assertIn("without adding a core object model", roadmap)


if __name__ == "__main__":
    unittest.main()
