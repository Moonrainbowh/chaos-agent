import unittest
from code_agent.capabilities.compact_tools import compact_definitions, expand_request
from code_agent.core.models import ActionRequest, ToolDefinition


def tool(name):
    return ToolDefinition(name, name, {"type":"object","properties":{"path":{"type":"string"}},"required":["path"],"additionalProperties":False})


class CompactToolsTests(unittest.TestCase):
    def test_restricted_snapshot_only_exposes_allowed_operations(self):
        tools = (tool("read_file"),tool("plan_workspace_edits_v1"))
        definitions = compact_definitions(tools)
        self.assertEqual([t.name for t in definitions],["read","edit"])
        self.assertEqual(tuple(definitions[1].parameters["properties"]["operation"]["enum"]),("plan",))
        request = expand_request(ActionRequest("id","edit",{"operation":"plan","path":"a"}),tools)
        self.assertEqual(request.name,"plan_workspace_edits_v1")
        self.assertEqual(request.id,"id")
        with self.assertRaises(ValueError):
            expand_request(ActionRequest("id","edit",{"operation":"apply","path":"a"}),tools)

    def test_missing_and_unrelated_arguments_are_rejected(self):
        for args in ({"operation":"file"},{"operation":"file","path":"a","content":"b"},{"operation":"unknown"}):
            with self.subTest(args=args),self.assertRaises(ValueError):
                expand_request(ActionRequest("id","read",args),(tool("read_file"),))
