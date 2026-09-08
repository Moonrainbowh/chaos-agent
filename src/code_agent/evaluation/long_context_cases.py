"""Ten heterogeneous repair contracts, with a separate pilot contract."""
from dataclasses import dataclass
import json
import random


@dataclass(frozen=True)
class LongContextCase:
    identifier: str
    contract: str
    correct_source: str
    faulty_source: str
    inputs: tuple

    def oracle(self):
        namespace = {}
        exec(self.correct_source, namespace)
        return tuple({"input": value, "expected": namespace["solve"](value)} for value in self.inputs)


def _case(identifier, contract, source, old, new, inputs):
    if source.count(old) != 1:
        raise ValueError("fixture defect anchor is ambiguous")
    return LongContextCase(identifier, contract, source, source.replace(old, new), tuple(inputs))


def long_context_cases():
    return (
        _case("01-normalization", "Normalize a list: ignore None, strip edges, casefold Unicode; discard empty strings; deduplicate AFTER casefold; preserve first encounter order. Return strings.",
              'def solve(values):\n    result = []\n    for value in values:\n        if value is None:\n            continue\n        item = value.strip().casefold()\n        if item and item not in result:\n            result.append(item)\n    return result\n',
              '.casefold()', '.lower()', [["Straße", "STRASSE", " x ", "X"], [None, " ", "A"], ["İ", "i", "SS", "ß"], []]),
        _case("02-ledger", "Process event dictionaries in encounter order. A job becomes committed only after status='success'. Failed attempts do NOT consume its idempotency key. Output job IDs exactly once, in order of first success.",
              'def solve(events):\n    done, out = set(), []\n    for event in events:\n        key = event["job"]\n        if event["status"] == "success" and key not in done:\n            done.add(key)\n            out.append(key)\n    return out\n',
              'event["status"] == "success" and key not in done', 'key not in done',
              [[{"job":"a","status":"failed"},{"job":"b","status":"success"},{"job":"a","status":"success"}], [{"job":"x","status":"failed"}], []]),
        _case("03-money", "Input price, quantity, discount, tax are decimal strings or integer quantity. Compute price*quantity*(1-discount)*(1+tax), with no intermediate rounding. Round final amount to two decimal places using ROUND_HALF_UP, return fixed-two-decimal string. Negative amounts follow the same rule.",
              'from decimal import Decimal, ROUND_HALF_UP\ndef solve(x):\n    value = Decimal(x["price"]) * x["quantity"] * (1-Decimal(x["discount"])) * (1+Decimal(x["tax"]))\n    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))\n',
              'rounding=ROUND_HALF_UP', 'rounding="ROUND_HALF_EVEN"',
              [{"price":p,"quantity":q,"discount":d,"tax":t} for p,q,d,t in [("1.005",1,"0","0"),("-1.005",1,"0","0"),("0.335",3,"0","0"),("99.95",7,".125",".07")]]),
        _case("04-retries", "Return delays in integer milliseconds for attempts 0..attempts-1. Delay=min(cap_ms, base_ms*2**attempt). Attempt zero uses base, cap applies to every attempt, no jitter. attempts=0 returns empty.",
              'def solve(x):\n    return [min(x["cap_ms"], x["base_ms"] * 2**i) for i in range(x["attempts"])]\n',
              '2**i', '2**(i+1)',
              [{"base_ms":b,"cap_ms":c,"attempts":a} for b,c,a in [(100,750,8),(1000,500,3),(0,100,4),(5,5,0)]]),
        _case("05-cache", "Given now and cache records key/value/expires_at, return dictionary of live entries. Expiration is exclusive: expires_at == now is EXPIRED. None means no expiration. Later live duplicate key overrides earlier live value; expired duplicates do not delete a live value.",
              'def solve(x):\n    result = {}\n    for row in x["records"]:\n        if row["expires_at"] is None or row["expires_at"] > x["now"]:\n            result[row["key"]] = row["value"]\n    return result\n',
              '> x["now"]', '>= x["now"]',
              [{"now":10,"records":[{"key":"a","value":1,"expires_at":None},{"key":"a","value":2,"expires_at":10},{"key":"b","value":3,"expires_at":11}]}, {"now":0,"records":[]}]),
        _case("06-routing", "Routes contain prefix,target. Match path case-sensitively at segment boundary (path==prefix or starts prefix.rstrip('/')+'/'). Choose longest matching prefix; ties keep first. Root '/' matches all slash paths. No match returns None.",
              'def solve(x):\n    matches = []\n    for index, row in enumerate(x["routes"]):\n        prefix = row["prefix"].rstrip("/")\n        if x["path"] == prefix or x["path"].startswith(prefix + "/"):\n            matches.append((len(prefix), -index, row["target"]))\n    return max(matches)[2] if matches else None\n',
              'startswith(prefix + "/")', 'startswith(prefix)',
              [{"path":p,"routes":[{"prefix":"/","target":"root"},{"prefix":"/api","target":"api"},{"prefix":"/api/v1","target":"v1"}]} for p in ["/apian","/api","/api/v1/x","/API","plain"]]),
        _case("07-intervals", "Input half-open integer intervals [start,end]. Drop empty/reversed intervals. Sort by start then end and union overlapping intervals. Touching intervals must REMAIN SEPARATE because they record distinct episodes. Return lists of endpoints.",
              'def solve(rows):\n    out = []\n    for start, end in sorted(rows):\n        if start >= end:\n            continue\n        if out and start < out[-1][1]:\n            out[-1][1] = max(out[-1][1], end)\n        else:\n            out.append([start, end])\n    return out\n',
              'start < out[-1][1]', 'start <= out[-1][1]',
              [[[1,3],[3,5]], [[8,10],[1,4],[2,3],[3,6],[9,12],[0,0]], [], [[4,2],[-2,0],[0,1]]]),
        _case("08-weighted", "Input rows value/weight. Ignore rows with value=None and rows with weight<=0. Weighted mean denominator includes only accepted weights. No accepted rows returns None. Otherwise return numeric weighted average; zero value is valid.",
              'def solve(rows):\n    accepted = [r for r in rows if r["value"] is not None and r["weight"] > 0]\n    weight = sum(r["weight"] for r in accepted)\n    return sum(r["value"]*r["weight"] for r in accepted)/weight if weight else None\n',
              'r["value"] is not None', 'r["value"]',
              [[{"value":0,"weight":2},{"value":9,"weight":1}], [{"value":None,"weight":5},{"value":5,"weight":-2}], [], [{"value":2,"weight":1},{"value":8,"weight":3}]]),
        _case("09-csv", "Return CSV for a list of string rows. Use comma delimiter, double quote quoting, double embedded quotes, minimal quoting and LF line terminators. Preserve embedded newlines and Unicode. An empty rows list returns empty string.",
              'import csv, io\ndef solve(rows):\n    target = io.StringIO(newline="")\n    csv.writer(target, lineterminator="\\n").writerows(rows)\n    return target.getvalue()\n',
              'lineterminator="\\n"', 'lineterminator="\\r\\n"',
              [[["a,b","c\"d"],["line\nbreak","中文"]], [[""]], [], [["a","b"]]]),
        _case("10-pagination", "Input items dictionaries id/score, offset, limit. Sort descending score, then ascending integer id. Slice offset:offset+limit. Return IDs. limit=0 means no items, and large offset means empty.",
              'def solve(x):\n    items = sorted(x["items"], key=lambda row: (-row["score"], row["id"]))\n    return [r["id"] for r in items[x["offset"]:x["offset"]+x["limit"]]]\n',
              '(-row["score"], row["id"])', '(-row["score"], -row["id"])',
              [{"items":[{"id":i,"score":s} for i,s in [(5,3),(2,3),(1,2),(9,3)]],"offset":o,"limit":l} for o,l in [(0,4),(1,2),(3,0),(20,2)]]),
    )


def pilot_case():
    return _case("pilot-only", "Return the median of a nonempty integer list. Even lengths use the arithmetic mean of the middle two values. Do not mutate input.",
                 'def solve(values):\n    values = sorted(values)\n    n = len(values)\n    return values[n//2] if n%2 else (values[n//2-1]+values[n//2])/2\n',
                 'values = sorted(values)', 'values = list(values)', [[9,1,5], [4,1,3,2], [0]])


def history_documents(case, counter, target_tokens):
    """Deterministic synthetic audit evidence, not claimed as model-generated work."""
    rng = random.Random(20260905 + sum(map(ord, case.identifier)))
    documents = {"contract.md": case.contract, "solution.py": case.faulty_source}
    namespace = {}
    exec(case.correct_source, namespace)
    per_shard = target_tokens // 8
    for shard in range(8):
        lines, tokens, number = [], 0, 0
        while tokens < per_shard:
            value = _public_input(case.identifier, rng)
            sample = {"input": value, "expected": namespace["solve"](value)}
            line = json.dumps({"audit": f"{shard}:{number}", "revision": rng.randrange(1000,9999),
                               "contract": case.identifier, **sample,
                               "review": "Expected output derived from accepted contract; current implementation still needs verification."}, ensure_ascii=False)
            lines.append(line)
            tokens += counter.text(line + "\n")
            number += 1
        documents[f"audit/part-{shard}.jsonl"] = "\n".join(lines)
    return documents


def _public_input(identifier, rng):
    kind = identifier.split("-")[0]
    n = rng.randrange(100, 100000)
    values = {
        "01": [f" Item{n} ", f"ITEM{n}", None, ""],
        "02": [{"job": f"job{n}", "status": rng.choice(["failed", "success"])}, {"job": f"job{n+1}", "status": "success"}],
        "03": {"price": str(n/1000), "quantity": rng.randrange(1,8), "discount": ".15", "tax": ".075"},
        "04": {"base_ms": n, "cap_ms": n*5, "attempts": rng.randrange(1,8)},
        "05": {"now": n, "records": [{"key": f"key{n}", "value": n+3, "expires_at": n+rng.randrange(-1,2)}]},
        "06": {"path": f"/docs/{n}/page", "routes": [{"prefix": "/docs", "target": f"doc{n}"}, {"prefix": "/", "target": "root"}]},
        "07": [[n,n+5],[n+3,n+7],[n+7,n+8]],
        "08": [{"value": n, "weight": 2}, {"value": 0, "weight": rng.randrange(1,8)}],
        "09": [[f"item{n}", f"value,{n+1}"], [f"record{n+2}", "sample"]],
        "10": {"items": [{"id":n,"score":5},{"id":n+1,"score":5},{"id":n+2,"score":7}], "offset":0,"limit":2},
        "pilot": [n+3,n,n+5],
    }
    return values[kind]
