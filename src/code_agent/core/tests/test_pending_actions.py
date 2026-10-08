import unittest
from code_agent.core.models import Message, ToolCall
from code_agent.core.pending_actions import pending_calls


class PendingCallsTests(unittest.TestCase):
    def test_result_must_follow_and_match_call_identity(self):
        call = ToolCall('id', 'write_file', {'path': 'a', 'content': 'b'})
        assistant = Message('assistant', tool_calls=(call,))
        result = Message('tool', '{}', name='write_file', tool_call_id='id')
        self.assertEqual(pending_calls((assistant,)), (call,))
        self.assertEqual(pending_calls((result, assistant)), (call,))
        self.assertEqual(pending_calls((assistant, Message('tool', '{}', name='read_file', tool_call_id='id'))), (call,))
        self.assertEqual(pending_calls((assistant, result)), ())

    def test_ambiguous_duplicate_call_ids_cannot_clear_each_other(self):
        call = ToolCall('id', 'write_file', {'path': 'a', 'content': 'b'})
        assistant = Message('assistant', tool_calls=(call,))
        result = Message('tool', '{}', name='write_file', tool_call_id='id')
        self.assertEqual(pending_calls((assistant, result, assistant)), (call, call))
