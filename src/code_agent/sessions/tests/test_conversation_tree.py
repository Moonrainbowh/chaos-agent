import tempfile
import unittest
from pathlib import Path
import sqlite3
from contextlib import closing

from code_agent.core.models import Message, ToolCall
from code_agent.sessions.repository import SQLiteSessionRepository


class ConversationTreeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"sessions.db"
        self.sessions = SQLiteSessionRepository(self.path)
        self.root = await self.sessions.create_thread()

    async def asyncTearDown(self):
        self.sessions.close()
        self.temp.cleanup()

    async def test_fork_keeps_original_and_only_selected_prefix(self):
        for message in (Message("user","question"),Message("assistant","answer"),Message("user","old branch")):
            await self.sessions.append_message(self.root,message)
        tree = await self.sessions.load_conversation_tree(self.root)
        fork = await self.sessions.fork_conversation(self.root,tree.nodes[1].id)
        await self.sessions.append_message(fork,Message("user","new branch"))
        self.assertEqual([m.content for m in await self.sessions.load_messages(fork)],["question","answer","new branch"])
        self.assertEqual([m.content for m in await self.sessions.load_messages(self.root)],["question","answer","old branch"])
        tree = await self.sessions.load_conversation_tree(fork)
        self.assertEqual(len(tree.nodes),4)
        self.assertEqual(tree.nodes[-1].parent_id,tree.nodes[1].id)
        await self.sessions.label_conversation_node(fork,tree.nodes[-1].id,"bookmark")
        reloaded = SQLiteSessionRepository(self.path)
        self.assertEqual((await reloaded.load_conversation_tree(fork)).nodes[-1].label,"bookmark")
        reloaded.close()

    async def test_continuation_preserves_canonical_nodes_and_nested_forks(self):
        await self.sessions.append_message(self.root,Message("user","root"))
        node = (await self.sessions.load_conversation_tree(self.root)).nodes[0]
        fork = await self.sessions.fork_conversation(self.root,node.id)
        continuation = await self.sessions.create_thread_from_history(fork)
        await self.sessions.append_message(continuation,Message("assistant","continued"))
        tree = await self.sessions.load_conversation_tree(continuation)
        self.assertEqual(len(tree.nodes),2)
        nested = await self.sessions.fork_conversation(continuation,tree.nodes[-1].id)
        await self.sessions.append_message(nested,Message("user","nested"))
        self.assertEqual(len((await self.sessions.load_conversation_tree(self.root)).nodes),3)

    async def test_rejects_incomplete_tool_pair_and_foreign_node_atomically(self):
        await self.sessions.append_message(self.root,Message("assistant","",tool_calls=(ToolCall("call","read_file",{"path":"a"}),)))
        first = (await self.sessions.load_conversation_tree(self.root)).nodes[0]
        with self.assertRaises(ValueError):
            await self.sessions.fork_conversation(self.root,first.id)
        await self.sessions.append_message(self.root,Message("tool","result",tool_call_id="call"))
        last = (await self.sessions.load_conversation_tree(self.root)).nodes[-1]
        fork = await self.sessions.fork_conversation(self.root,last.id)
        self.assertEqual(len(await self.sessions.load_messages(fork)),2)
        other = await self.sessions.create_thread()
        with self.assertRaises(Exception):
            await self.sessions.fork_conversation(other,last.id)

    async def test_migrates_v23_without_changing_original_messages(self):
        await self.sessions.append_message(self.root,Message("user","existing"))
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            for table in ("conversation_heads","conversation_message_refs","conversation_nodes"):
                connection.execute("DROP TABLE "+table)
            connection.execute("PRAGMA user_version=23")
            connection.commit()
        migrated = SQLiteSessionRepository(self.path)
        self.assertEqual((await migrated.load_conversation_tree(self.root)).nodes[0].message.content,"existing")
        migrated.close()
