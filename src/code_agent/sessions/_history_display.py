"""Stable display fragments and literal retrieval without fetching whole blobs."""
import json
from ._codec import decode_message
from ._history_queries import bounded, ensure_item_ids, stable_item_id
from ._records import _require_thread


def record_history_display(connection, sequence, message):
    display = json.dumps(message.to_dict(), ensure_ascii=False)
    ranges=[]
    folded=0
    for original,character in enumerate(message.content):
        width=len(character.casefold())
        if not ranges or ranges[-1][3]!=width:
            ranges.append([folded,folded,original,width])
        folded+=width
        ranges[-1][1]=folded
    connection.execute("INSERT OR REPLACE INTO history_display(sequence,display,content_fold,fold_ranges) VALUES (?,?,?,?)",
        (sequence,display,message.content.casefold(),json.dumps(ranges,separators=(",",":"))))


def ensure_display(connection, thread, sequence=None):
    """Legacy display backfill is one paged pass; all hot fragment reads use substr."""
    while True:
        where = "m.thread_id=? AND d.sequence IS NULL"
        args = [thread]
        if sequence is not None:
            where += " AND m.sequence=?"
            args.append(sequence)
        rows = connection.execute(f"SELECT m.sequence,length(CAST(m.payload AS BLOB)) AS size FROM messages m LEFT JOIN history_display d ON d.sequence=m.sequence WHERE {where} ORDER BY m.sequence LIMIT 100", args).fetchall()
        if not rows:
            return
        for row in rows:
            if row["size"]>16*1024*1024:
                raise ValueError("legacy history display exceeds bounded migration capacity")
            payload = connection.execute("SELECT payload FROM messages WHERE thread_id=? AND sequence=?", (thread,row["sequence"])).fetchone()[0]
            record_history_display(connection,row["sequence"],decode_message(payload))


class HistoryDisplayRepositoryMixin:
    async def history_item_fragment(self, thread_id, item_id, *, offset=0, max_chars=4000):
        bounded(offset,2**31-1,"offset",zero=True)
        bounded(max_chars,16000,"max_chars")
        def read(c):
            _require_thread(c,thread_id)
            row = c.execute("SELECT sequence FROM history_item_ids WHERE thread_id=? AND item_id=?", (thread_id,item_id)).fetchone()
            if row is None:
                ensure_item_ids(c,thread_id)
                row = c.execute("SELECT sequence FROM history_item_ids WHERE thread_id=? AND item_id=?",(thread_id,item_id)).fetchone()
            if row is None:
                return None
            sequence = row[0]
            ensure_display(c,thread_id,sequence)
            row = c.execute("SELECT substr(d.display,?,?),length(d.display),json_extract(m.payload,'$.role') FROM history_display d JOIN messages m ON m.sequence=d.sequence WHERE m.thread_id=? AND d.sequence=?", (offset+1,max_chars,thread_id,sequence)).fetchone()
            if offset>row[1]:
                raise ValueError("history fragment offset exceeds message length")
            end=min(row[1],offset+max_chars)
            return {"item_id":item_id,"sequence":sequence,"role":row[2],"offset":offset,
                "text":row[0],"next_offset":end if end<row[1] else None}
        return await self._database.write(read)

    async def search_history_page(self, thread_id, query, *, after_sequence=0,
            before_sequence=None,role=None,limit=20,offset=0,content_only=False,casefold=False):
        if not isinstance(query,str) or len(query)>12000:
            raise ValueError("invalid history query")
        bounded(after_sequence,2**63-1,"after_sequence",zero=True)
        bounded(limit,1000,"limit")
        bounded(offset,2**31-1,"offset",zero=True)
        if before_sequence is not None:
            bounded(before_sequence,2**63-1,"before_sequence",zero=True)
        if role not in {None,"user","assistant","tool","system","developer"}:
            raise ValueError("invalid history role")
        if casefold and not content_only:
            raise ValueError("casefold is supported for compatible content-only history")
        def read(c):
            _require_thread(c,thread_id)
            ensure_display(c,thread_id)
            expression = "d.content_fold" if casefold else "json_extract(m.payload,'$.content')" if content_only else "d.display"
            output = "json_extract(m.payload,'$.content')" if content_only else "d.display"
            needle = query.casefold() if casefold else query
            where,args = "m.thread_id=? AND m.sequence>?",[thread_id,after_sequence]
            if before_sequence is not None:
                where+=" AND m.sequence<?"
                args.append(before_sequence)
            if role:
                where+=" AND json_extract(m.payload,'$.role')=?"
                args.append(role)
            # Empty literal means list; SQL handles matching, only snippets enter Python.
            where+=f" AND instr({expression},?)>0" if needle else ""
            if needle:
                args.append(needle)
            found=f"instr({expression},?)-1" if needle else "0"
            if casefold and needle:
                # SQL maps casefold expansion (e.g. ß→ss) back to original
                # Unicode character offsets before slicing original content.
                folded_position=found
                found=f"COALESCE((SELECT json_extract(j.value,'$[2]')+CAST(({folded_position}-json_extract(j.value,'$[0]'))/json_extract(j.value,'$[3]') AS INTEGER) FROM json_each(d.fold_ranges) j WHERE json_extract(j.value,'$[1]')>{folded_position} ORDER BY CAST(j.key AS INTEGER) LIMIT 1),0)"
            select_args=[needle] if needle else []
            if casefold and needle:
                select_args=[needle,needle]
            rows=c.execute(f"SELECT m.sequence,json_extract(m.payload,'$.role') AS role,MAX(0,{found}-120) AS fragment_offset FROM messages m JOIN history_display d ON d.sequence=m.sequence WHERE {where} ORDER BY m.sequence LIMIT ? OFFSET ?", (*select_args,*args,limit+1,offset)).fetchall()
            items=[]
            for row in rows[:limit]:
                text=c.execute(f"SELECT substr({output},?,600) FROM messages m JOIN history_display d ON d.sequence=m.sequence WHERE m.thread_id=? AND m.sequence=?", (row["fragment_offset"]+1,thread_id,row["sequence"])).fetchone()[0]
                items.append({"sequence":row["sequence"],"item_id":stable_item_id(thread_id,row["sequence"]),
                    "role":row["role"],"offset":row["fragment_offset"],"snippet":text})
            return {"items":items,"next_sequence":items[-1]["sequence"] if len(rows)>limit else None,
                "next_offset":offset+limit if len(rows)>limit else None}
        return await self._database.write(read)
