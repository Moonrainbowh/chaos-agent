"""Recovery summaries use metadata projections and SQL counts, never full journals."""
import json
from ._records import _require_thread


def recovery_auxiliary(c,task_id,thread):
    _require_thread(c,thread)
    header=c.execute("SELECT id,length(CAST(metadata AS BLOB)) FROM checkpoints WHERE thread_id=? AND label NOT LIKE 'context:%' ORDER BY created_at DESC,id DESC LIMIT 1",(thread,)).fetchone()
    latest=None
    if header:
        if header[1]>1048576:
            raise ValueError("latest checkpoint exceeds bounded recovery capacity")
        row=c.execute("SELECT id,label,message_sequence,event_sequence,metadata FROM checkpoints WHERE thread_id=? AND id=?",(thread,header[0])).fetchone()
        latest={"id":row[0],"label":row[1],"message_sequence":row[2],"event_sequence":row[3],"metadata":json.loads(row[4])}
    # Working-note content is not needed in a recovery checklist.
    latest_notes="""WITH versions AS (SELECT rowid AS source_rowid,ROW_NUMBER() OVER (
        PARTITION BY json_extract(metadata,'$.path') ORDER BY rowid DESC) AS n
        FROM checkpoints WHERE thread_id=? AND label='context:note_file')
        SELECT c.rowid AS source_rowid,COALESCE(length(CAST(json_extract(c.metadata,'$.path') AS BLOB)),0)+
          COALESCE(length(CAST(json_extract(c.metadata,'$.coverage') AS BLOB)),0) AS size
        FROM versions v JOIN checkpoints c ON c.rowid=v.source_rowid WHERE v.n=1 LIMIT 1001"""
    headers=c.execute(latest_notes,(thread,)).fetchall()
    if len(headers)>1000 or sum(row['size'] for row in headers)>1048576:
        raise ValueError("recovery note references exceed bounded capacity")
    selected=[]
    for header in headers:
        row=c.execute("SELECT json_extract(metadata,'$.path'),json_extract(metadata,'$.revision'),json_extract(metadata,'$.coverage') FROM checkpoints WHERE thread_id=? AND rowid=?",(thread,header['source_rowid'])).fetchone()
        value={"path":row[0],"revision":row[1]}
        if row[2] is not None:
            value["coverage"]=json.loads(row[2])
        selected.append(value)
    return {"latest_checkpoint":latest,"notes":tuple(sorted(selected,key=lambda value:value["path"])),
        "pending_followups":c.execute("SELECT COUNT(*) FROM task_followups WHERE task_id=?",(task_id,)).fetchone()[0],
        "verification_evidence_count":c.execute("SELECT COUNT(*) FROM verification_evidence WHERE task_id=?",(task_id,)).fetchone()[0]}
