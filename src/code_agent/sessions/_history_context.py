"""Bounded window/checkpoint metadata and SQL usage aggregates."""
import json
import uuid
from ._history_queries import bounded, history_stats
from ._records import _require_thread
from ._codec import decode_metadata


def metadata_page(c, thread, label, *, limit=100,before_rowid=None,after_rowid=0,
        newest=False,max_bytes=1048576,offset=0):
    bounded(limit,1000,"limit")
    bounded(max_bytes,16*1024*1024,"max_bytes")
    bounded(after_rowid,2**63-1,"after_rowid",zero=True)
    bounded(offset,2**31-1,"offset",zero=True)
    if before_rowid is not None:
        bounded(before_rowid,2**63-1,"before_rowid",zero=True)
    _require_thread(c,thread)
    where,args="thread_id=? AND label=? AND rowid>?",[thread,label,after_rowid]
    if before_rowid is not None:
        where+=" AND rowid<?"
        args.append(before_rowid)
    order="DESC" if newest else "ASC"
    headers=c.execute(f"SELECT rowid AS _rowid,length(CAST(metadata AS BLOB)) AS size FROM checkpoints WHERE {where} ORDER BY rowid {order} LIMIT ? OFFSET ?", (*args,limit,offset)).fetchall()
    if sum(row["size"] for row in headers)>max_bytes:
        raise ValueError("checkpoint metadata exceeds bounded byte capacity")
    result=[]
    for row in headers:
        record=c.execute("SELECT id,metadata FROM checkpoints WHERE thread_id=? AND rowid=?", (thread,row["_rowid"])).fetchone()
        result.append({"id":record[0],**dict(decode_metadata(record[1])),"_rowid":row["_rowid"]})
    return tuple(result)


def window_bounds(c,thread,identifier):
    first=uuid.uuid5(uuid.NAMESPACE_URL,f"chaos:window:{thread}:initial").hex
    bounds=c.execute("SELECT MIN(sequence),MAX(sequence) FROM messages WHERE thread_id=?",(thread,)).fetchone()
    if identifier==first:
        next_row=c.execute("SELECT json_extract(metadata,'$.start_sequence') FROM checkpoints WHERE thread_id=? AND label='context:window' ORDER BY rowid LIMIT 1",(thread,)).fetchone()
        return {"window_id":identifier,"start":bounds[0] or 1,"end":next_row[0]-1 if next_row else bounds[1] or 0}
    row=c.execute("SELECT rowid,json_extract(metadata,'$.start_sequence') FROM checkpoints WHERE thread_id=? AND label='context:window' AND id=?",(thread,identifier)).fetchone()
    if row is None:
        return None
    next_row=c.execute("SELECT json_extract(metadata,'$.start_sequence') FROM checkpoints WHERE thread_id=? AND label='context:window' AND rowid>? ORDER BY rowid LIMIT 1",(thread,row[0])).fetchone()
    return {"window_id":identifier,"start":row[1],"end":next_row[0]-1 if next_row else bounds[1] or 0}


class HistoryContextRepositoryMixin:
    async def context_note_page(self,thread_id,*,identifier=None,query=None,offset=0,limit=1,max_bytes=1048576):
        bounded(offset,2**31-1,"offset",zero=True)
        bounded(limit,1000,"limit")
        bounded(max_bytes,16*1024*1024,"max_bytes")
        if query is not None and (not isinstance(query,str) or len(query)>12000):
            raise ValueError("invalid note query")
        def read(c):
            _require_thread(c,thread_id)
            if c.execute("SELECT 1 FROM checkpoints WHERE thread_id=? AND label='context:note' AND length(CAST(metadata AS BLOB))>131072 LIMIT 1",(thread_id,)).fetchone():
                raise ValueError("note metadata exceeds bounded capacity")
            c.create_function("history_casefold",1,lambda value:value.casefold() if isinstance(value,str) else "")
            where,args="thread_id=? AND label='context:note'",[thread_id]
            if identifier is not None:
                where+=" AND id=?"
                args.append(identifier)
            if query:
                where+=" AND instr(history_casefold(json_extract(metadata,'$.text')),?)>0"
                args.append(query.casefold())
            count=c.execute(f"SELECT COUNT(*) FROM checkpoints WHERE {where}",args).fetchone()[0]
            headers=c.execute(f"SELECT id,length(CAST(metadata AS BLOB)) AS size FROM checkpoints WHERE {where} ORDER BY rowid LIMIT ? OFFSET ?",(*args,limit,offset)).fetchall()
            if sum(row['size'] for row in headers)>max_bytes:
                raise ValueError("note page exceeds byte capacity")
            items=[]
            for row in headers:
                text=c.execute("SELECT metadata FROM checkpoints WHERE thread_id=? AND id=?",(thread_id,row['id'])).fetchone()[0]
                items.append({"id":row['id'],**dict(decode_metadata(text))})
            return {"items":items,"next_offset":offset+len(items) if offset+len(items)<count else None}
        return await self._database.read(read)

    async def context_record_page(self,thread_id,kind,**kwargs):
        if kind not in {"window","request","note","usage"}:
            raise ValueError("invalid context record kind")
        return await self._database.read(lambda c:metadata_page(c,thread_id,"context:"+kind,**kwargs))

    async def context_record_item(self,thread_id,kind,identifier,*,max_bytes=1048576):
        bounded(max_bytes,16*1024*1024,"max_bytes")
        def read(c):
            _require_thread(c,thread_id)
            row=c.execute("SELECT rowid,length(CAST(metadata AS BLOB)) FROM checkpoints WHERE thread_id=? AND label=? AND id=?",(thread_id,"context:"+kind,identifier)).fetchone()
            if row is None:
                return None
            if row[1]>max_bytes:
                raise ValueError("context metadata exceeds byte capacity")
            return metadata_page(c,thread_id,"context:"+kind,after_rowid=row[0]-1,before_rowid=row[0]+1,limit=1,max_bytes=max_bytes)[0]
        return await self._database.read(read)

    async def context_record_for_sequence(self,thread_id,sequence,kind="window"):
        bounded(sequence,2**63-1,"sequence",zero=True)
        def read(c):
            _require_thread(c,thread_id)
            row=c.execute("SELECT rowid FROM checkpoints WHERE thread_id=? AND label=? AND json_extract(metadata,'$.start_sequence')<=? ORDER BY rowid DESC LIMIT 1",(thread_id,"context:"+kind,sequence)).fetchone()
            return None if row is None else metadata_page(c,thread_id,"context:"+kind,after_rowid=row[0]-1,before_rowid=row[0]+1,limit=1)[0]
        return await self._database.read(read)

    async def context_usage_totals(self,thread_id):
        def read(c):
            _require_thread(c,thread_id)
            from ._shared_budget import binding
            bound=binding(c,thread_id)
            owner=bound["owner_thread_id"] if bound else thread_id
            where,args="thread_id=? AND label='context:usage'",[owner]
            if bound:
                where+=" AND COALESCE(json_extract(metadata,'$.origin_thread_id'),?)=?"
                args.extend((owner,thread_id))
            row=c.execute(f"SELECT COUNT(*),COALESCE(SUM(COALESCE(json_extract(metadata,'$.charged'),0)+COALESCE(json_extract(metadata,'$.prior_usage'),0)),0),COALESCE(SUM(CASE WHEN json_extract(metadata,'$.status')<>'settled' THEN COALESCE(json_extract(metadata,'$.reserved'),0) ELSE 0 END),0) FROM checkpoints WHERE {where}",args).fetchone()
            first=c.execute(f"SELECT json_extract(metadata,'$.task_limit') FROM checkpoints WHERE {where} ORDER BY rowid LIMIT 1",args).fetchone()
            return {"count":row[0],"spent":row[1],"reserved":row[2],"task_limit":first[0] if first else None}
        return await self._database.read(read)

    async def semantic_checkpoint_page(self,thread_id,*,limit=100,offset=0,newest=False,max_bytes=1048576,excluded_ranges=()):
        bounded(limit,1000,"limit")
        bounded(offset,2**31-1,"offset",zero=True)
        bounded(max_bytes,16*1024*1024,"max_bytes")
        excluded_ranges=tuple(excluded_ranges)
        if len(excluded_ranges)>128:
            raise ValueError("semantic selected ranges exceed bounded capacity")
        for start,end in excluded_ranges:
            bounded(start,2**63-1,"source start")
            bounded(end,2**63-1,"source end")
            if end<start:
                raise ValueError("invalid semantic source range")
        def read(c):
            _require_thread(c,thread_id)
            order="DESC" if newest else "ASC"
            where,args="thread_id=?",[thread_id]
            for start,end in excluded_ranges:
                where+=" AND NOT COALESCE((json_extract(payload,'$.source_start.sequence')<=? AND json_extract(payload,'$.source_end.sequence')>=?),0)"
                args.extend((end,start))
            headers=c.execute(f"SELECT rowid,length(CAST(payload AS BLOB)) FROM semantic_checkpoints WHERE {where} ORDER BY created_at {order},id {order} LIMIT ? OFFSET ?",(*args,limit,offset)).fetchall()
            if sum(row[1] for row in headers)>max_bytes:
                raise ValueError("semantic checkpoint page exceeds byte capacity")
            from ._semantic import _decode_checkpoint
            return tuple(_decode_checkpoint(c.execute("SELECT payload FROM semantic_checkpoints WHERE thread_id=? AND rowid=?",(thread_id,row[0])).fetchone()[0]) for row in headers)
        return await self._database.read(read)

    async def context_window_bounds(self,thread_id,window_id):
        def read(c):
            _require_thread(c,thread_id)
            return window_bounds(c,thread_id,window_id)
        return await self._database.read(read)

    async def context_window_page(self,thread_id,*,offset=0,limit=10):
        bounded(offset,2**31-1,"offset",zero=True)
        bounded(limit,1000,"limit")
        def read(c):
            _require_thread(c,thread_id)
            total=1+c.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id=? AND label='context:window'",(thread_id,)).fetchone()[0]
            ids=[]
            if offset==0:
                ids.append(uuid.uuid5(uuid.NAMESPACE_URL,f"chaos:window:{thread_id}:initial").hex)
            count=limit-len(ids)
            if count:
                ids.extend(row[0] for row in c.execute("SELECT id FROM checkpoints WHERE thread_id=? AND label='context:window' ORDER BY rowid LIMIT ? OFFSET ?",(thread_id,count,max(0,offset-1))).fetchall())
            return {"items":[window_bounds(c,thread_id,identifier) for identifier in ids],
                "next_offset":offset+len(ids) if offset+len(ids)<total else None}
        return await self._database.read(read)
