"""Event pages retain durable sequence cursors without decoding whole journals."""
from ._history_queries import bounded
from ._records import _require_thread
from ._codec import decode_event,decode_datetime


class HistoryEventRepositoryMixin:
    async def read_event_page(self,thread_id,*,after_sequence=0,before_sequence=None,
            limit=100,max_bytes=1048576,newest=False):
        bounded(after_sequence,2**63-1,"after_sequence",zero=True)
        bounded(limit,1000,"limit")
        bounded(max_bytes,16*1024*1024,"max_bytes")
        if before_sequence is not None:
            bounded(before_sequence,2**63-1,"before_sequence",zero=True)
        def read(c):
            _require_thread(c,thread_id)
            where,args="thread_id=? AND sequence>?",[thread_id,after_sequence]
            if before_sequence is not None:
                where+=" AND sequence<?"
                args.append(before_sequence)
            order="DESC" if newest else "ASC"
            headers=c.execute(f"SELECT sequence,length(CAST(payload AS BLOB)) AS size FROM events WHERE {where} ORDER BY sequence {order} LIMIT ?",(*args,limit)).fetchall()
            selected,size=[],0
            for row in headers:
                if size+row['size']>max_bytes:
                    if not selected:
                        raise ValueError("required history event exceeds byte budget")
                    break
                selected.append(row['sequence'])
                size+=row['size']
            if not selected:
                return ()
            marks=",".join("?" for _ in selected)
            rows=c.execute(f"SELECT sequence,payload,created_at FROM events WHERE thread_id=? AND sequence IN ({marks}) ORDER BY sequence",(thread_id,*selected)).fetchall()
            return tuple({"sequence":row['sequence'],"thread_id":thread_id,
                "event":decode_event(row['payload']),"created_at":decode_datetime(row['created_at'],"event")} for row in rows)
        return await self._database.read(read)


def bounded_record_rows(c,table,thread,*,limit=None,max_bytes=None,label=None):
    """Legacy unbounded reads remain explicit; bounded requests preflight all fields."""
    if table not in {"goals","checkpoints"}:
        raise ValueError("invalid record table")
    where="thread_id=?"+(" AND label NOT LIKE 'context:%'" if table=="checkpoints" else "")
    args=[thread]
    if label is not None:
        if table!="checkpoints" or not isinstance(label,str) or not label.strip():
            raise ValueError("invalid checkpoint label filter")
        where+=" AND label=?"
        args.append(label)
    if limit is None and max_bytes is None:
        return c.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY created_at,id",args).fetchall()
    count=1000 if limit is None else bounded(limit,1000,"limit")
    capacity=1048576 if max_bytes is None else bounded(max_bytes,16*1024*1024,"max_bytes")
    fields=("objective","metadata","id","created_at","updated_at") if table=="goals" else ("label","metadata","id","created_at")
    size="+".join(f"length(CAST({field} AS BLOB))" for field in fields)
    headers=c.execute(f"SELECT id,{size} AS size FROM {table} WHERE {where} ORDER BY created_at DESC,id DESC LIMIT ?",(*args,count)).fetchall()
    if sum(row['size'] for row in headers)>capacity:
        raise ValueError("history records exceed bounded byte capacity")
    if not headers:
        return ()
    marks=",".join("?" for _ in headers)
    return c.execute(f"SELECT * FROM {table} WHERE thread_id=? AND id IN ({marks}) ORDER BY created_at,id",(thread,*(row['id'] for row in headers))).fetchall()
