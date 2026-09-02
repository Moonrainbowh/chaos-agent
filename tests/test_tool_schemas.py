from __future__ import annotations

import codecs
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent_win.app import RootActionDispatcher  # noqa: E402
from code_agent_win.tools import tool_definitions  # noqa: E402
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class ToolSchemaTests(unittest.TestCase):
    def test_public_tools_have_strict_provider_compatible_object_schemas(self) -> None:
        definitions = {
            tool.name: tool.to_dict()["parameters"] for tool in tool_definitions()
        }

        self.assertEqual(
            set(definitions),
            {
                "load_tool_contract",
                "read_file",
                "read_code_slices",
                "list_files",
                "search_text",
                "write_file",
                "replace_text",
                "plan_workspace_edits_v1",
                "apply_workspace_edit_plan_v1",
                "git_status",
                "git_diff",
                "run_verification",
                "run_command",
                "run_process_v1",
                "delegate_agent",
            },
        )
        for parameters in definitions.values():
            self.assertEqual(parameters["type"], "object")
            self.assertFalse(parameters["additionalProperties"])
            self.assertIsInstance(parameters["required"], list)
            self.assertIsInstance(parameters["properties"], dict)

        self.assertEqual(definitions["load_tool_contract"]["required"], ["name"])
        self.assertEqual(definitions["read_file"]["required"], ["path"])
        self.assertEqual(
            definitions["read_code_slices"]["required"], ["generation", "targets"]
        )
        self.assertEqual(
            definitions["read_code_slices"]["properties"]["targets"]["maxItems"], 16
        )
        self.assertEqual(definitions["read_file"]["properties"]["path"]["type"], "string")
        text_encodings = ["auto", "windows-ansi", "windows-oem"]
        for name in ("read_file", "write_file", "replace_text"):
            self.assertEqual(
                definitions[name]["properties"]["encoding"]["enum"],
                text_encodings,
            )
        self.assertEqual(definitions["write_file"]["required"], ["path", "content"])
        self.assertEqual(definitions["replace_text"]["required"], ["path", "old_text", "new_text"])
        self.assertEqual(definitions["run_command"]["required"], ["command"])
        self.assertEqual(
            definitions["run_process_v1"]["required"], ["program", "args"]
        )
        self.assertEqual(
            definitions["run_process_v1"]["properties"]["args"]["maxItems"], 128
        )
        self.assertTrue(
            {"shell", "env", "stdin"}.isdisjoint(
                definitions["run_process_v1"]["properties"]
            )
        )
        self.assertEqual(definitions["run_verification"]["required"], ["kind"])
        self.assertEqual(definitions["delegate_agent"]["required"], ["objective"])
        self.assertIn("agent_id", definitions["delegate_agent"]["properties"])
        self.assertEqual(definitions["git_diff"]["properties"]["paths"]["type"], "array")
        self.assertEqual(definitions["git_diff"]["properties"]["paths"]["items"]["type"], "string")
        self.assertEqual(definitions["git_diff"]["properties"]["paths"]["items"]["minLength"], 1)


class DispatcherValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "note.txt").write_text("before\n", encoding="utf-8")
        guard = WorkspacePathGuard(self.root)
        self.policy = Mock(wraps=ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)))
        self.files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.dispatcher = RootActionDispatcher(
            self.files,
            WorkspaceEditor(guard),
            self.policy,
            ApprovalBroker(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_malformed_write_is_rejected_before_policy_or_file_side_effect(self) -> None:
        result = await self.dispatcher.dispatch(
            ActionRequest("call-1", "write_file", {"path": "note.txt", "content": ""}),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error"], "invalid tool arguments")
        self.policy.evaluate.assert_not_called()
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "before\n")

    async def test_unknown_property_is_rejected_before_runtime_side_effect(self) -> None:
        runtime = Mock()
        result = await RootActionDispatcher(
            self.files,
            WorkspaceEditor(WorkspacePathGuard(self.root)),
            self.policy,
            ApprovalBroker(),
            runtime=runtime,
        ).dispatch(
            ActionRequest("call-2", "run_command", {"command": "Get-Date", "unsafe": True}),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.policy.evaluate.assert_not_called()
        runtime.run.assert_not_called()

    async def test_read_reports_detected_text_format(self) -> None:
        (self.root / "note.txt").write_bytes(
            codecs.BOM_UTF16_LE + "before\r\n".encode("utf-16-le")
        )

        result = await self.dispatcher.dispatch(
            ActionRequest("read-format", "read_file", {"path": "note.txt"}),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["text"], "before\r\n")
        self.assertEqual(result.output["encoding"], "utf-16-le")
        self.assertIs(result.output["bom"], True)
        self.assertEqual(result.output["newline"], "crlf")
        self.assertNotIn("code_page", result.output)

    async def test_contract_loader_returns_current_schema_without_execution(self) -> None:
        result = await self.dispatcher.dispatch(
            ActionRequest(
                "load-read", "load_tool_contract", {"name": "read_file"}
            ),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["contract"]["name"], "read_file")
        self.assertEqual(result.output["availability"], "next_model_turn")
        self.assertEqual(result.metadata["disclosed_tool"], "read_file")

    async def test_write_preserves_and_reports_existing_text_format(self) -> None:
        (self.root / "note.txt").write_bytes(
            codecs.BOM_UTF8 + b"before\r\n"
        )

        result = await self.dispatcher.dispatch(
            ActionRequest(
                "write-format",
                "write_file",
                {"path": "note.txt", "content": "after\n"},
            ),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(
            (self.root / "note.txt").read_bytes(),
            codecs.BOM_UTF8 + b"after\r\n",
        )
        self.assertEqual(result.output["encoding"], "utf-8")
        self.assertIs(result.output["bom"], True)
        self.assertEqual(result.output["newline"], "crlf")

    @unittest.skipUnless(os.name == "nt", "Windows code pages are Windows-only")
    async def test_explicit_windows_encoding_reaches_read_and_write(self) -> None:
        for encoding in ("windows-ansi", "windows-oem"):
            with self.subTest(encoding=encoding):
                (self.root / "note.txt").write_bytes(b"legacy\r\n")
                read = await self.dispatcher.dispatch(
                    ActionRequest(
                        f"read-{encoding}",
                        "read_file",
                        {"path": "note.txt", "encoding": encoding},
                    ),
                    CancellationToken(),
                )
                write = await self.dispatcher.dispatch(
                    ActionRequest(
                        f"write-{encoding}",
                        "write_file",
                        {
                            "path": "note.txt",
                            "content": "changed\n",
                            "encoding": encoding,
                        },
                    ),
                    CancellationToken(),
                )

                for result in (read, write):
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.output["encoding"], encoding)
                    self.assertGreater(result.output["code_page"], 0)
                self.assertEqual(
                    (self.root / "note.txt").read_bytes(), b"changed\r\n"
                )

    async def test_malformed_peer_calls_are_rejected_before_policy_or_approval(self) -> None:
        peers = Mock()
        dispatcher = RootActionDispatcher(
            self.files,
            WorkspaceEditor(WorkspacePathGuard(self.root)),
            self.policy,
            ApprovalBroker(),
            peers=peers,
        )
        malformed = (
            ActionRequest("peer-list", "list_agents", {"detail": True}),
            ActionRequest(
                "peer-target-blank",
                "send_message",
                {"target": "   ", "message": "secret body"},
            ),
            ActionRequest(
                "peer-target-long",
                "send_message",
                {"target": "t" * 81, "message": "secret body"},
            ),
            ActionRequest(
                "peer-message-large",
                "send_message",
                {"target": "receiver", "message": chr(0x1F600) * 4_097},
            ),
            ActionRequest(
                "peer-message-invalid-unicode",
                "send_message",
                {"target": "receiver", "message": "\ud800"},
            ),
        )

        for request in malformed:
            with self.subTest(request=request.id):
                result = await dispatcher.dispatch(request, CancellationToken())
                self.assertTrue(result.is_error)
                self.assertEqual(result.output["error"], "invalid tool arguments")
                self.assertNotIn("secret body", str(result.output))

        self.policy.evaluate.assert_not_called()
        peers.dispatch.assert_not_called()
