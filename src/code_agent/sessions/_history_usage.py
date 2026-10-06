"""SQL request aggregation of provider snapshots, without conversation materialization."""
import json

from code_agent.core.models import Usage
from ._records import _require_thread


class HistoryUsageRepositoryMixin:
    async def conversation_usage_summary(self, thread_id, *, conversation=True):
        """Return UsageSummary fields; bound individual snapshots and model metadata.

        Request groups follow MODEL_STARTED and replace earlier usage snapshots.
        SQLite scans event facts; Python receives only aggregates, latest Usage,
        and at most 128 model identifiers (64KiB combined).
        """
        if not isinstance(conversation,bool):
            raise TypeError("conversation must be a boolean")
        def read(c):
            _require_thread(c, thread_id)
            if conversation:
                head=c.execute("SELECT conversation_id FROM conversation_heads WHERE thread_id=?",(thread_id,)).fetchone()
                group=head[0] if head else thread_id
                scope="""(e.thread_id=? OR e.thread_id IN
                    (SELECT thread_id FROM conversation_heads WHERE conversation_id=?))"""
                args=(thread_id,group)
            else:
                scope="e.thread_id=?"
                args=(thread_id,)
            # Only a small usage object enters Python; body text stays in SQLite.
            def normalize(raw):
                try:
                    value=json.loads(raw)
                    if not isinstance(value,dict) or not value:
                        return None
                    normalized=Usage.from_dict(value).to_dict()
                except (ValueError,TypeError,KeyError):
                    return None
                if any(isinstance(item,int) and item>2**63-1 for item in normalized.values()):
                    raise OverflowError("provider token count exceeds exact SQLite aggregation")
                return json.dumps(normalized,separators=(",",":"))
            c.create_function("history_valid_usage",1,normalize)
            candidates=f"""SELECT e.sequence,e.payload FROM events e WHERE {scope}
                AND json_valid(e.payload) AND json_extract(e.payload,'$.kind') IN ('model_started','model_event')"""
            oversized=c.execute(f"""SELECT 1 FROM ({candidates})
                WHERE json_extract(payload,'$.kind')='model_event'
                AND json_extract(payload,'$.payload.event.kind')='usage'
                AND length(CAST(json_extract(payload,'$.payload.event.usage') AS BLOB))>4096 LIMIT 1""",args).fetchone()
            if oversized:
                raise ValueError("provider usage snapshot exceeds bounded capacity")
            models_sql=f"""SELECT DISTINCT json_extract(payload,'$.payload.model') AS model
                FROM ({candidates}) WHERE json_extract(payload,'$.kind')='model_started'
                AND json_type(payload,'$.payload.model')='text'"""
            model_budget=c.execute(f"SELECT COUNT(*),COALESCE(SUM(length(CAST(model AS BLOB))),0) FROM ({models_sql})",args).fetchone()
            if model_budget[0]>128 or model_budget[1]>65536:
                raise ValueError("provider model metadata exceeds bounded capacity")
            models=frozenset(row[0] for row in c.execute(models_sql,args))
            prefix=f"""WITH facts AS MATERIALIZED (
                SELECT sequence,
                    json_extract(payload,'$.kind')='model_started' AS started,
                    CASE WHEN json_extract(payload,'$.kind')='model_event'
                        AND json_extract(payload,'$.payload.event.kind')='usage'
                        AND json_type(payload,'$.payload.event.usage')='object'
                        AND COALESCE(json_type(payload,'$.payload.event.text'),'null')='null'
                        AND (COALESCE(json_type(payload,'$.payload.event.tool_call'),'null')='null'
                             OR (json_type(payload,'$.payload.event.tool_call') IN ('integer','real','false')
                                 AND json_extract(payload,'$.payload.event.tool_call')=0)
                             OR (json_type(payload,'$.payload.event.tool_call')='text'
                                 AND json_extract(payload,'$.payload.event.tool_call')='')
                             OR (json_type(payload,'$.payload.event.tool_call')='object'
                                 AND json_extract(payload,'$.payload.event.tool_call')='{{}}')
                             OR (json_type(payload,'$.payload.event.tool_call')='array'
                                 AND json_extract(payload,'$.payload.event.tool_call')='[]'))
                        THEN history_valid_usage(json_extract(payload,'$.payload.event.usage')) END AS usage
                FROM ({candidates})
            ), grouped AS (
                SELECT sequence,started,usage,SUM(started) OVER (ORDER BY sequence) AS request FROM facts
            ), snapshots AS (
                SELECT sequence,request,usage,ROW_NUMBER() OVER(PARTITION BY request ORDER BY sequence DESC) AS rank
                FROM grouped WHERE usage IS NOT NULL
            ), known AS (SELECT sequence,request,usage FROM snapshots WHERE rank=1)
            """
            row=c.execute(prefix+"""SELECT
                COALESCE(SUM(json_extract(usage,'$.input_tokens')),0),
                COALESCE(SUM(json_extract(usage,'$.output_tokens')),0),
                COALESCE(SUM(json_extract(usage,'$.cached_input_tokens')),0),
                COALESCE(SUM(COALESCE(json_extract(usage,'$.cache_write_input_tokens'),0)),0),
                COUNT(*),COALESCE(MIN(COALESCE(json_extract(usage,'$.cache_read_known'),0)=1
                    OR json_extract(usage,'$.cached_input_tokens')>0),0),
                COALESCE(MIN(COALESCE(json_type(usage,'$.cache_write_input_tokens')='integer',0)),0),
                (SELECT COALESCE(MAX(request),0) FROM grouped)+(SELECT COUNT(*) FROM known WHERE request=0),
                (SELECT usage FROM known ORDER BY sequence DESC LIMIT 1)
                FROM known""",args).fetchone()
            return dict(zip(("input_tokens","output_tokens","cache_read","cache_write","requests",
                "read_known","write_known"),(*row[:5],bool(row[5]),bool(row[6]))),
                incomplete=row[4]<row[7],latest=Usage.from_dict(json.loads(row[8])) if row[8] else None,models=models)
        return await self._database.read(read)
