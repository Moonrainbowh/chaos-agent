"""S10 journal counterexamples use only asserted temporary SQLite paths."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from code_agent.core._session_io import SessionJournal
from code_agent.core.events import AgentEvent,EventKind
from code_agent.core.host_progress import observe_host_progress
from code_agent.core.models import ActionResult,Message,ToolCall
from code_agent.interfaces.task_controller import freeze_task_contract,authorization_for_task_mode
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.sessions._codec import encode_message,encode_datetime,utc_now
from code_agent.sessions._history_queries import stable_item_id
import code_agent.sessions._history_queries as queries
import code_agent.sessions._history_events as event_queries


class BoundedHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="chaos-s10-history-test-")
        self.root=Path(self.temp.name).resolve()
        self.path=(self.root/"sessions.sqlite3").resolve()
        assert self.path.parent==self.root and self.path.is_relative_to(self.root)
        self.repo=SQLiteSessionRepository(self.path)
        self.thread=await self.repo.create_thread()

    async def asyncTearDown(self):
        self.repo.close()
        self.temp.cleanup()

    async def append_pair(self,identifier="call",path="src/a.py",text="A"):
        call=ToolCall(identifier,"read_file",{"path":path})
        await self.repo.append_message(self.thread,Message("assistant",tool_calls=(call,)))
        result=ActionResult(identifier,call.name,{"content":text})
        await self.repo.append_message(self.thread,Message("tool",json.dumps(result.to_dict()),name=call.name,tool_call_id=identifier))

    def seed(self,messages):
        stamp=encode_datetime(utc_now())
        with closing(sqlite3.connect(self.path)) as c,c:
            c.executemany("INSERT INTO messages(thread_id,payload,created_at) VALUES (?,?,?)",((self.thread,encode_message(m),stamp) for m in messages))

    async def task(self):
        return await self.repo.create_task(self.thread,freeze_task_contract("Investigate src/",authorization_for_task_mode(str(self.root),"code"),None))

    async def test_sql_page_decodes_only_twenty_and_sequence_gaps_are_not_counts(self):
        self.seed([Message("user",f"row{i}") for i in range(600)])
        other=await self.repo.create_thread()
        await self.repo.append_message(other,Message("user","other"))
        await self.repo.append_message(self.thread,Message("user","last"))
        original=queries.decode_message
        with patch.object(queries,"decode_message",wraps=original) as decode:
            page=await self.repo.read_history_page(self.thread,limit=20,newest=True)
            self.assertEqual(decode.call_count,20)
        stats=await self.repo.history_stats(self.thread)
        self.assertEqual(stats["message_count"],601)
        self.assertGreater(stats["message_sequence"],stats["message_count"])
        older=await self.repo.read_history_page(self.thread,before_sequence=page[0].sequence,limit=20,newest=True)
        self.assertLess(older[-1].sequence,page[0].sequence)

    async def test_pending_old_call_survives_pages_and_duplicate_id_stays_pending(self):
        call=ToolCall("old","read_file",{"path":"a.py"})
        await self.repo.append_message(self.thread,Message("assistant",tool_calls=(call,)))
        self.seed([Message("user","noise") for _ in range(200)])
        self.assertEqual((await self.repo.pending_action_records(self.thread))[0]["tool_call_id"],"old")
        await self.repo.append_message(self.thread,Message("tool","{}",name="other",tool_call_id="old"))
        self.assertEqual(len(await self.repo.pending_action_records(self.thread)),1)
        await self.repo.append_message(self.thread,Message("tool","{}",name="read_file",tool_call_id="old"))
        self.assertEqual(await self.repo.pending_action_records(self.thread),())
        await self.repo.append_message(self.thread,Message("assistant",tool_calls=(call,)))
        await self.repo.append_message(self.thread,Message("tool","{}",name="read_file",tool_call_id="old"))
        self.assertEqual(len(await self.repo.pending_action_records(self.thread)),2)

    async def test_result_before_call_never_pairs(self):
        await self.repo.append_message(self.thread,Message("tool","{}",name="read_file",tool_call_id="old"))
        await self.repo.append_message(self.thread,Message("assistant",tool_calls=(ToolCall("old","read_file",{}),)))
        self.assertEqual(len(await self.repo.pending_action_records(self.thread)),1)

    async def test_nonassistant_call_metadata_duplicate_keeps_original_global_id_guard(self):
        call=ToolCall("old","read_file",{})
        await self.repo.append_message(self.thread,Message("user","legacy",tool_calls=(call,)))
        await self.repo.append_message(self.thread,Message("assistant",tool_calls=(call,)))
        await self.repo.append_message(self.thread,Message("tool","{}",name=call.name,tool_call_id=call.id))
        self.assertTrue(await self.repo.has_tool_call_id(self.thread,"old"))
        self.assertEqual(len(await self.repo.pending_action_records(self.thread)),1)

    async def test_tail_expands_tool_group_and_keeps_latest_required_user(self):
        await self.repo.append_message(self.thread,Message("user","required"))
        calls=(ToolCall("a","read_file",{}),ToolCall("b","read_file",{}))
        await self.repo.append_message(self.thread,Message("assistant",tool_calls=calls))
        for call in calls:
            await self.repo.append_message(self.thread,Message("tool","{}",name=call.name,tool_call_id=call.id))
        messages=await self.repo.load_context_messages(self.thread,limit=1)
        self.assertEqual([m.role for m in messages],["user","assistant","tool","tool"])

    async def test_giant_pending_arguments_fail_before_decoding_and_required_message_fails(self):
        await self.repo.append_message(self.thread,Message("assistant",tool_calls=(ToolCall("giant","read_file",{"path":"x"*1100000}),)))
        with patch.object(queries.json,"loads",side_effect=AssertionError("must not decode giant arguments")):
            with self.assertRaisesRegex(ValueError,"byte capacity"):
                await self.repo.pending_action_records(self.thread)
        with self.assertRaisesRegex(ValueError,"byte budget"):
            await self.repo.read_history_page(self.thread,limit=1)

    async def test_large_fragment_sql_substr_and_unicode_offsets_reopen(self):
        message=Message("user",'中文😀"quoted"\n'+'x'*1100000)
        await self.repo.append_message(self.thread,message)
        sequence=(await self.repo.history_stats(self.thread))["message_sequence"]
        identity=stable_item_id(self.thread,sequence)
        expected=json.dumps(message.to_dict(),ensure_ascii=False)
        with patch.object(queries,"decode_message",side_effect=AssertionError("fragment cannot decode entire message")):
            first=await self.repo.history_item_fragment(self.thread,identity,max_chars=19)
            second=await self.repo.history_item_fragment(self.thread,identity,offset=19,max_chars=21)
        self.assertEqual(first["text"]+second["text"],expected[:40])
        self.repo.close()
        assert self.path.is_relative_to(self.root)
        self.repo=SQLiteSessionRepository(self.path)
        self.assertEqual((await self.repo.history_item_fragment(self.thread,identity,max_chars=19))["text"],expected[:19])
        other=await self.repo.create_thread()
        self.assertIsNone(await self.repo.history_item_fragment(other,identity))

    async def test_legacy_display_literal_arguments_and_unicode_search(self):
        message=Message("assistant",tool_calls=(ToolCall("call","read_file",{"path":"中文😀.py"}),))
        self.seed([message])
        listed=await self.repo.search_history_page(self.thread,'"path": "中文😀.py"')
        self.assertEqual(len(listed["items"]),1)
        identity=listed["items"][0]["item_id"]
        self.assertEqual((await self.repo.read_history_item(self.thread,item_id=identity)).message,message)
        fragment=await self.repo.history_item_fragment(self.thread,identity,max_chars=16000)
        self.assertEqual(fragment["text"],json.dumps(message.to_dict(),ensure_ascii=False))

    async def test_casefold_expansion_keeps_original_unicode_character_offset(self):
        content="ß"*130+" needle 中文😀"
        await self.repo.append_message(self.thread,Message("user",content))
        result=await self.repo.search_history_page(self.thread,"NEEDLE",content_only=True,casefold=True)
        item=result["items"][0]
        self.assertEqual(item["offset"],content.index("needle")-120)
        self.assertEqual(item["snippet"],content[item["offset"]:][:600])

    async def test_context_note_unicode_search_and_giant_coverage_rejected_before_fetch(self):
        await self.repo.append_context_record(self.thread,"note","key",{"text":"Straße 中文😀"})
        found=await self.repo.context_note_page(self.thread,query="STRASSE")
        self.assertEqual(len(found["items"]),1)
        task=await self.task()
        metadata=json.dumps({"path":"notes.md","revision":1,"coverage":{"extra":"x"*1100000}})
        with closing(sqlite3.connect(self.path)) as c,c:
            c.execute("INSERT INTO checkpoints(id,thread_id,label,metadata,created_at) VALUES (?,?,?,?,?)",("oversize",self.thread,"context:note_file",metadata,encode_datetime(utc_now())))
        with self.assertRaisesRegex(ValueError,"bounded capacity"):
            await self.repo.recovery_checklist(task.id)

    async def test_core_cold_replay_hot_zero_decode_and_restart_incremental(self):
        await self.repo.append_message(self.thread,Message("user","Investigate src/"))
        for i in range(6):
            await self.append_pair(str(i),f"src/{i}.py",str(i))
        journal=SessionJournal(self.repo)
        kwargs=dict(candidate_limit=24,hard_tool_limit=256)
        first,revision=await journal.host_progress(self.thread,"Investigate src/",None,**kwargs)
        original=queries.decode_message
        with patch.object(queries,"decode_message",wraps=original) as decode:
            second,_=await journal.host_progress(self.thread,"Investigate src/",None,**kwargs)
            self.assertEqual(decode.call_count,0)
        self.assertEqual(first,second)
        self.assertEqual(revision,1)
        self.repo.close()
        assert self.path.is_relative_to(self.root)
        self.repo=SQLiteSessionRepository(self.path)
        await self.append_pair("new","src/new.py","new")
        with patch.object(queries,"decode_message",wraps=original) as decode:
            third,_=await SessionJournal(self.repo).host_progress(self.thread,"Investigate src/",None,**kwargs)
            self.assertEqual(decode.call_count,2)
        self.assertEqual(third.related_count,first.related_count+1)

    async def test_progress_hot_return_rechecks_same_sequence_update(self):
        await self.append_pair()
        journal=SessionJournal(self.repo)
        kwargs=dict(candidate_limit=24,hard_tool_limit=256)
        before,_=await journal.host_progress(self.thread,"Investigate src/",None,**kwargs)
        original=self.repo.history_stats
        count=0
        async def changed_stats(thread):
            nonlocal count
            stats=await original(thread)
            count+=1
            if count==1:
                message=Message("tool",json.dumps(ActionResult("call","read_file",{"content":"B"}).to_dict()),name="read_file",tool_call_id="call")
                await self.repo._database.write(lambda c:c.execute("UPDATE messages SET payload=? WHERE thread_id=? AND sequence=?",(encode_message(message),thread,stats["message_sequence"])))
            return stats
        with patch.object(self.repo,"history_stats",changed_stats):
            after,_=await journal.host_progress(self.thread,"Investigate src/",None,**kwargs)
        self.assertNotEqual(before.renewal_digest,after.renewal_digest)
        self.assertGreaterEqual(count,3)

    async def test_recovery_same_sequence_payload_change_rejects_old_version(self):
        task=await self.task()
        call=ToolCall("pending","read_file",{})
        await self.repo.append_message(self.thread,Message("assistant",tool_calls=(call,)))
        before=await self.repo.recovery_checklist(task.id)
        with closing(sqlite3.connect(self.path)) as c,c:
            c.execute("UPDATE messages SET created_at=? WHERE thread_id=?",(encode_datetime(utc_now()),self.thread))
        after=await self.repo.recovery_checklist(task.id)
        self.assertNotEqual(before["recovery_version"],after["recovery_version"])
        with self.assertRaisesRegex(ValueError,"stale recovery version"):
            await self.repo.resolve_pending_action(task.id,call_id="pending",message_sequence=before["pending_action_records"][0]["message_sequence"],version=before["recovery_version"],workspace_root=str(self.root),decision="operator_not_executed",reason="review",evidence="checked",operator_authorized=True)

    async def test_event_only_growth_is_notes_lagging_and_checklist_avoids_all_loads(self):
        task=await self.task()
        with patch.object(self.repo,"load_messages",side_effect=AssertionError("all load forbidden")),patch.object(self.repo,"load_events",side_effect=AssertionError("all load forbidden")),patch.object(self.repo,"load_message_records",side_effect=AssertionError("all load forbidden")):
            await self.repo.append_event(self.thread,AgentEvent(EventKind.RUN_STARTED,{}))
            facts=await self.repo.recovery_checklist(task.id)
        self.assertTrue(facts["notes_lagging"])
        self.assertGreater(facts["event_count"],0)

    async def test_result_noise_and_running_do_not_evict_one_another(self):
        task=await self.task()
        await self.repo.append_event(self.thread,AgentEvent(EventKind.TASK_STATUS_CHANGED,{"task_id":task.id,"status":"running","run_instance_id":"new"}))
        for i in range(40):
            await self.repo.append_event(self.thread,AgentEvent(EventKind.TASK_RESULT,{"task_id":task.id,"result_generation":0,"result_subject_hash":"","result":{"tag":str(i)}}))
        for _ in range(40):
            await self.repo.append_event(self.thread,AgentEvent(EventKind.MODEL_EVENT,{}))
        events=await self.repo.load_latest_task_events(task.id,0,"")
        self.assertEqual(len(events),33)
        self.assertEqual(events[0].payload["run_instance_id"],"new")
        for i in range(40):
            await self.repo.append_event(self.thread,AgentEvent(EventKind.TASK_STATUS_CHANGED,{"task_id":task.id,"status":"running","run_instance_id":str(i)}))
        events=await self.repo.load_latest_task_events(task.id,0,"")
        self.assertEqual(events[-1].payload["run_instance_id"],"39")
        self.assertTrue(any(e.kind is EventKind.TASK_RESULT for e in events))

    async def test_null_subject_result_projection_keeps_latest_running_and_results(self):
        task=await self.task()
        await self.repo.append_event(self.thread,AgentEvent(EventKind.TASK_STATUS_CHANGED,{"task_id":task.id,"status":"running","run_instance_id":"new"}))
        for i in range(50):
            await self.repo.append_event(self.thread,AgentEvent(EventKind.TASK_RESULT,{"task_id":task.id,"result_generation":0,"result_subject_hash":None,"result":{"status":"cancelled" if i%2 else "accepted_partial","tag":str(i)}}))
        events=await self.repo.load_latest_task_events(task.id,0,None)
        self.assertEqual(len(events),33)
        self.assertEqual(events[0].payload["run_instance_id"],"new")
        self.assertEqual(events[-1].payload["result"]["tag"],"49")
        self.assertEqual(len(await self.repo.load_latest_task_events(task.id,0,"other")),1)

    async def test_conversation_usage_sql_matches_accumulator_without_loading_event_bodies(self):
        from code_agent.interfaces.usage_summary import summarize_usage,UsageSummary
        events=[
            AgentEvent(EventKind.MODEL_EVENT,{"event":{"kind":"usage","usage":{"input_tokens":3}}}),
            AgentEvent(EventKind.MODEL_STARTED,{"model":"one"}),
            AgentEvent(EventKind.MODEL_STARTED,{"model":"two"}),
            AgentEvent(EventKind.MODEL_EVENT,{"event":{"kind":"usage","usage":{"input_tokens":5,"cache_write_input_tokens":0}}}),
            AgentEvent(EventKind.MODEL_EVENT,{"event":{"kind":"usage","usage":{"input_tokens":8,"output_tokens":2,"cached_input_tokens":1,"cache_write_input_tokens":4}}}),
            AgentEvent(EventKind.MODEL_EVENT,{"event":{"kind":"usage","usage":{"input_tokens":True}}}),
            AgentEvent(EventKind.MODEL_EVENT,{"event":{"kind":"text_delta","text":"x"*1100000}}),
            AgentEvent(EventKind.MODEL_STARTED,{"model":"two"}),
        ]
        sibling=await self.repo.create_thread()
        for i,event in enumerate(events):
            await self.repo.append_event(self.thread if i<4 else sibling,event)
        await self.repo._database.write(lambda c:c.executemany("INSERT OR REPLACE INTO conversation_heads(thread_id,conversation_id,node_id) VALUES (?,?,NULL)",[(self.thread,self.thread),(sibling,self.thread)]))
        with patch.object(self.repo,"load_conversation_events",side_effect=AssertionError("no events load")),patch.object(self.repo,"load_conversation_tree",side_effect=AssertionError("no tree")):
            actual=UsageSummary(**await self.repo.conversation_usage_summary(self.thread))
        self.assertEqual(actual,summarize_usage(events))
        self.assertEqual(UsageSummary(**await self.repo.conversation_usage_summary(self.thread,conversation=False)),summarize_usage(events[:4]))
        self.assertEqual(UsageSummary(**await self.repo.conversation_usage_summary(sibling,conversation=False)),summarize_usage(events[4:]))
        self.assertEqual(actual.requests,2)
        self.assertTrue(actual.incomplete)
        self.assertFalse(actual.write_known)
        empty=await self.repo.create_thread()
        self.assertEqual(UsageSummary(**await self.repo.conversation_usage_summary(empty)),UsageSummary())

    async def test_conversation_usage_rejects_giant_usage_before_python_decode(self):
        await self.repo.append_event(self.thread,AgentEvent(EventKind.MODEL_EVENT,{"event":{"kind":"usage","usage":{"input_tokens":1,"unused":"x"*10000}}}))
        import code_agent.sessions._history_usage as usage_queries
        with patch.object(usage_queries.json,"loads",side_effect=AssertionError("no giant usage decode")):
            with self.assertRaisesRegex(ValueError,"snapshot exceeds"):
                await self.repo.conversation_usage_summary(self.thread)

    async def test_usage_tool_call_json_types_match_original_snapshot_validation(self):
        from code_agent.interfaces.usage_summary import UsageSummary,summarize_usage
        for tool_call in ('{}','[]','false','0','bad',{},[],False,0,0.0,'',None):
            with self.subTest(tool_call=tool_call):
                thread=await self.repo.create_thread()
                events=[AgentEvent(EventKind.MODEL_STARTED,{"model":"one"}),
                    AgentEvent(EventKind.MODEL_EVENT,{"event":{"kind":"usage","usage":{"input_tokens":10,"output_tokens":2}}}),
                    AgentEvent(EventKind.MODEL_EVENT,{"event":{"kind":"usage","usage":{"input_tokens":1000,"output_tokens":200},"tool_call":tool_call}})]
                for event in events:
                    await self.repo.append_event(thread,event)
                actual=UsageSummary(**await self.repo.conversation_usage_summary(thread))
                self.assertEqual(actual,summarize_usage(events))

    async def test_progress_lease_candidate_limit_change_replays_once_then_stays_incremental(self):
        await self.repo.append_message(self.thread,Message("user","Investigate src/"))
        for i in range(3):
            await self.append_pair(str(i),f"src/{i}.py",str(i))
        journal=SessionJournal(self.repo)
        first,_=await journal.host_progress(self.thread,"Investigate src/",None,candidate_limit=2,hard_tool_limit=128)
        self.assertEqual(first.candidate_count,2)
        original=queries.decode_message
        with patch.object(queries,"decode_message",wraps=original) as decode:
            upgraded,_=await journal.host_progress(self.thread,"Investigate src/",None,candidate_limit=3,hard_tool_limit=128)
            self.assertEqual(decode.call_count,7)
        expected=observe_host_progress(await self.repo.load_messages(self.thread),"Investigate src/",None,candidate_limit=3,hard_tool_limit=128)
        self.assertEqual(upgraded,expected)
        self.assertEqual(upgraded.related_count,first.related_count)
        with patch.object(queries,"decode_message",wraps=original) as decode:
            again,_=await journal.host_progress(self.thread,"Investigate src/",None,candidate_limit=3,hard_tool_limit=128)
            self.assertEqual(decode.call_count,0)
        self.assertEqual(again,upgraded)
        shrunk,_=await journal.host_progress(self.thread,"Investigate src/",None,candidate_limit=2,hard_tool_limit=128)
        self.assertEqual(shrunk,first)
        changed,_=await journal.host_progress(self.thread,"Investigate other/",None,candidate_limit=2,hard_tool_limit=128)
        self.assertEqual(changed.related_count,0)
        self.assertEqual(changed,observe_host_progress(await self.repo.load_messages(self.thread),"Investigate other/",None,candidate_limit=2,hard_tool_limit=128))

    async def test_incremental_reducer_matches_one_shot_across_call_result_page_boundary(self):
        call=ToolCall("a","read_file",{"path":"src/a.py"})
        result=ActionResult(call.id,call.name,{"content":"same"})
        messages=(Message("user","Investigate src/"),Message("assistant",tool_calls=(call,)),Message("tool",json.dumps(result.to_dict()),name=call.name,tool_call_id=call.id))
        state={}
        kwargs=dict(candidate_limit=24,hard_tool_limit=256)
        observe_host_progress(messages[:2],"Investigate src/",None,projection=state,**kwargs)
        incremental=observe_host_progress(messages[2:],"Investigate src/",None,projection=state,**kwargs)
        self.assertEqual(incremental,observe_host_progress(messages,"Investigate src/",None,**kwargs))

    async def test_events_and_legacy_metadata_queries_are_bounded_and_label_precedes_limit(self):
        for i in range(40):
            await self.repo.append_event(self.thread,AgentEvent(EventKind.MODEL_EVENT,{"number":i}))
            await self.repo.create_goal(self.thread,f"goal{i}")
            await self.repo.create_checkpoint(self.thread,f"ordinary{i}")
        original=event_queries.decode_event
        with patch.object(event_queries,"decode_event",wraps=original) as decode:
            events=await self.repo.read_event_page(self.thread,newest=True,limit=5)
            self.assertEqual(decode.call_count,5)
        self.assertEqual(events[0]["event"].payload["number"],35)
        older=await self.repo.read_event_page(self.thread,before_sequence=events[0]["sequence"],newest=True,limit=5)
        self.assertLess(older[-1]["sequence"],events[0]["sequence"])
        self.assertEqual(len(await self.repo.list_goals(self.thread,limit=3,max_bytes=10000)),3)
        selected=await self.repo.list_checkpoints(self.thread,limit=1,label="ordinary0",max_bytes=10000)
        self.assertEqual(selected[0].label,"ordinary0")
        self.assertEqual(len(await self.repo.list_checkpoints(self.thread)),40)
        await self.repo.create_goal(self.thread,"x"*1100000)
        with self.assertRaisesRegex(ValueError,"byte capacity"):
            await self.repo.list_goals(self.thread,limit=1,max_bytes=10000)

    async def test_corrupt_giant_projection_is_rejected_before_json_decode(self):
        await self.repo._database.write(lambda c:c.execute("INSERT INTO history_progress VALUES (?,?,?,?)",(self.thread,0,0,'"'+'x'*(17*1024*1024)+'"')))
        with patch.object(queries.json,"loads",side_effect=AssertionError("giant cache cannot reach JSON decode")):
            with self.assertRaisesRegex(ValueError,"bounded byte capacity"):
                await self.repo.load_host_progress_projection(self.thread)

    async def test_overlap_filter_does_not_hide_corrupt_checkpoint_with_missing_range(self):
        from code_agent.sessions.errors import SessionCorruptionError
        await self.repo._database.write(lambda c:c.execute("INSERT INTO semantic_checkpoints VALUES (?,?,?,?)",("bad",self.thread,"{}",encode_datetime(utc_now()))))
        with self.assertRaises(SessionCorruptionError):
            await self.repo.semantic_checkpoint_page(self.thread,newest=True,limit=1,excluded_ranges=((1,1),))
