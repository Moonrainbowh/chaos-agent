import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from code_agent.core.models import Message
from code_agent.sessions.conversation_tree import ConversationNode, ConversationTree
from code_agent.interfaces.session_tree import SessionTreeView
from code_agent.core.attachments import AttachmentRef
from code_agent.interfaces.attachment_input import AttachmentDraft


class TreeViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_selecting_user_message_restores_attachment_without_ingestion(self):
        ref = AttachmentRef("a"*64,"image/png",12,"original.png",2,3)
        node = ConversationNode(1,None,"t",Message("user","inspect",attachments=(ref,)),"")
        view = SessionTreeView(ConversationTree("t",1,(node,)))
        ingest = SimpleNamespace(ingest_paths=lambda:None,ingest_clipboard=lambda:None)
        draft = AttachmentDraft(ingest)
        app = SimpleNamespace(conversation_tree=SimpleNamespace(select=AsyncMock(return_value=("new","inspect"))),
            current_thread_id="t",restore_thread=AsyncMock(return_value=True),attachment_draft=draft,
            input=SimpleNamespace(replace=lambda _:None))
        await view.handle_key(app,"\r")
        self.assertEqual(draft.items,(ref,))
        self.assertEqual(draft.image_tokens,((ref.sha256,"[image1]"),))

    async def test_filter_search_collapse_and_restore_selection(self):
        tree = ConversationTree("group",3,(ConversationNode(1,None,"t",Message("user","root"),""),
            ConversationNode(2,1,"t",Message("assistant","answer"),""),
            ConversationNode(3,2,"t",Message("user","branch"),"")))
        view = SessionTreeView(tree)
        app = SimpleNamespace(conversation_tree=SimpleNamespace(select=AsyncMock(return_value=("new","branch"))),
                              current_thread_id="t",restore_thread=AsyncMock(return_value=True),input=SimpleNamespace(replace=lambda _:None))
        await view.handle_key(app,"\x0f")
        await view.handle_key(app,"\x0f")
        self.assertEqual([n.id for n,_ in view.visible()],[1,3])
        await view.handle_key(app,"b")
        self.assertEqual([n.id for n,_ in view.visible()],[3])
        await view.handle_key(app,"\r")
        self.assertFalse(view.active)
        app.restore_thread.assert_awaited_once_with("new")
