import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_cadence.model import DRIVER_WEIGHTS


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_SCRIPT = ROOT / "examples" / "adapter-template" / "adapter.py"
TEMPLATE_README = ROOT / "examples" / "adapter-template" / "README.md"
TEMPLATE_FIXTURES = ROOT / "examples" / "adapter-template" / "host-signal-fixtures"


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

    def test_host_session_signal_remains_template_local(self):
        for path in (ROOT / "codex_cadence").rglob("*.py"):
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn("HostSessionSignal", path.read_text(encoding="utf-8"))

    def test_adapter_template_driver_allowlist_tracks_task_sizing_model(self):
        template = load_template_module()
        self.assertEqual(template.SIGNAL_TASK_DRIVERS, set(DRIVER_WEIGHTS))

    def test_adapter_template_loads_generic_host_signal_fixture(self):
        template = load_template_module()
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "operator-stop.json"
            fixture.write_text(
                json.dumps(
                    {
                        "kind": "operator_stop",
                        "source": "generic-host-fixture",
                        "confidence": "medium",
                        "summary": "operator asked this host session to stop",
                        "task_type": "discovery",
                        "drivers": ["unknown_repo_area"],
                        "next_action": "Claim the generated handoff and inspect the preserved packet.",
                    }
                ),
                encoding="utf-8",
            )

            signal = template.load_host_signal_fixture(fixture)

        self.assertIsInstance(signal, template.HostSessionSignal)
        self.assertEqual(signal.kind, "operator_stop")
        self.assertEqual(signal.source, "generic-host-fixture")
        self.assertEqual(signal.confidence, "medium")
        self.assertEqual(signal.task_type, "discovery")
        self.assertEqual(signal.drivers, ("unknown_repo_area",))
        self.assertEqual(signal.summary, "operator asked this host session to stop")

    def test_adapter_template_null_host_signal_fixture_skips_cadence(self):
        template = load_template_module()
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "no-signal.json"
            fixture.write_text("null\n", encoding="utf-8")

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
                runner=lambda command, **_: calls.append(command) or {"unexpected": True},
                host_session_signal_detector=lambda: template.load_host_signal_fixture(fixture),
            )

        self.assertEqual(result["result"], "no_handoff_needed")
        self.assertEqual(calls, [])

    def test_adapter_template_cli_host_signal_file_maps_to_prepare_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            trace_path = tmp_path / "trace.json"
            fake_cadence = tmp_path / "fake_cadence.py"
            fake_cadence.write_text(
                "\n".join(
                    [
                        "import json",
                        "import os",
                        "import sys",
                        "trace_path = os.environ['FAKE_CADENCE_TRACE']",
                        "try:",
                        "    trace = json.loads(open(trace_path, encoding='utf-8').read())",
                        "except FileNotFoundError:",
                        "    trace = []",
                        "trace.append(sys.argv[1:])",
                        "open(trace_path, 'w', encoding='utf-8').write(json.dumps(trace))",
                        "command = next(arg for arg in sys.argv[1:] if arg in {'status', 'prepare-handoff'})",
                        "if command == 'status':",
                        "    print(json.dumps({'cadence': {'state': 'PLAY_ON'}}))",
                        "else:",
                        "    print(json.dumps({'stop_current_session': True, 'handoff': {'id': 'fixture-handoff', 'status': 'READY'}}))",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fixture = tmp_path / "operator-stop.json"
            fixture.write_text(
                json.dumps(
                    {
                        "kind": "operator_stop",
                        "source": "generic-host-fixture",
                        "confidence": "high",
                        "summary": "fixture summary wins",
                        "task_type": "discovery",
                        "drivers": ["unknown_repo_area", "cross_subsystem"],
                        "next_action": "fixture next action wins",
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["FAKE_CADENCE_TRACE"] = str(trace_path)

            result = subprocess.run(
                [
                    sys.executable,
                    str(TEMPLATE_SCRIPT),
                    "--runtime-root",
                    str(tmp_path / "runtime"),
                    "--repo",
                    "local/template",
                    "--cwd",
                    str(tmp_path),
                    "--handoff-id",
                    "fixture-handoff",
                    "--title",
                    "Fixture handoff",
                    "--summary",
                    "cli summary fallback",
                    "--next-action",
                    "cli next action fallback",
                    "--task-type",
                    "execution",
                    "--driver",
                    "multiple_files",
                    "--host-signal-file",
                    str(fixture),
                    "--cadence-command",
                    f'"{sys.executable}" "{fake_cadence}"',
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            trace = json.loads(trace_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["result"], "handoff_prepared")
        prepare_command = next(command for command in trace if "prepare-handoff" in command)
        self.assertEqual(prepare_command[prepare_command.index("--guardrail") + 1], "operator_stop")
        self.assertEqual(prepare_command[prepare_command.index("--task-type") + 1], "discovery")
        self.assertEqual(prepare_command[prepare_command.index("--summary") + 1], "fixture summary wins")
        self.assertEqual(prepare_command[prepare_command.index("--next-action") + 1], "fixture next action wins")
        self.assertEqual(
            [prepare_command[index + 1] for index, value in enumerate(prepare_command) if value == "--driver"],
            ["unknown_repo_area", "cross_subsystem"],
        )

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

    def test_adapter_template_default_detector_maps_cli_arguments(self):
        template = load_template_module()
        calls = []

        def fake_runner(command, *, runtime_root, cadence_command, **_):
            calls.append(command)
            if command[0] == "status":
                return {"cadence": {"state": "PLAY_ON"}}
            return {
                "stop_current_session": True,
                "handoff": {"id": "template-handoff", "status": "READY"},
            }

        template.prepare_context_handoff(
            runtime_root=Path("runtime"),
            repo="local/template",
            cwd=Path("."),
            handoff_id="template-handoff",
            title="Template handoff",
            summary="template summary",
            next_action="start from cli input",
            cadence_command=["agentic-cadence"],
            task_type="execution",
            drivers=["multiple_files"],
            runner=fake_runner,
        )

        prepare_command = calls[1]
        self.assertEqual(prepare_command[prepare_command.index("--guardrail") + 1], "context")
        self.assertEqual(prepare_command[prepare_command.index("--task-type") + 1], "execution")
        self.assertEqual(prepare_command[prepare_command.index("--driver") + 1], "multiple_files")
        self.assertEqual(prepare_command[prepare_command.index("--summary") + 1], "template summary")
        self.assertEqual(prepare_command[prepare_command.index("--next-action") + 1], "start from cli input")

    def test_adapter_template_maps_signal_kind_to_guardrail(self):
        template = load_template_module()

        for kind, guardrail in (("context_pressure", "context"), ("operator_stop", "operator_stop")):
            with self.subTest(kind=kind):
                calls = []

                def fake_runner(command, *, runtime_root, cadence_command, _calls=calls, **_):
                    _calls.append(command)
                    if command[0] == "status":
                        return {"cadence": {"state": "PLAY_ON"}}
                    return {
                        "stop_current_session": True,
                        "handoff": {"id": "template-handoff", "status": "READY"},
                    }

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
                    host_session_signal_detector=lambda kind=kind: self.signal(template, kind=kind),
                )

                prepare_command = calls[1]
                self.assertEqual(prepare_command[prepare_command.index("--guardrail") + 1], guardrail)

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
            ("kind", [], "kind"),
            ("source", "", "source"),
            ("source", "x" * 65, "source"),
            ("confidence", "certain", "confidence"),
            ("confidence", [], "confidence"),
            ("task_type", "maintenance", "task_type"),
            ("task_type", [], "task_type"),
            ("drivers", ("typo_driver",), "driver"),
            ("drivers", "reviewer_feedback", "drivers"),
            ("summary", "   ", "summary"),
            ("next_action", "", "next_action"),
        ]

        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                calls = []

                def fake_runner(command, _calls=calls, **_):
                    _calls.append(command)
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
        self.assertTrue((TEMPLATE_FIXTURES / "context-pressure.json").exists())
        self.assertTrue((TEMPLATE_FIXTURES / "operator-stop.json").exists())
        self.assertTrue((TEMPLATE_FIXTURES / "no-signal.json").exists())
        self.assertIn("--host-signal-file", readme)
        self.assertIn("host-signal-fixtures", readme)


if __name__ == "__main__":
    unittest.main()
