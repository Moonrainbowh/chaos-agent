"""Metrics use durable provider usage and host-observed file reads."""
def usage_metrics(rows):
    result = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
              "handoff_tokens": 0, "main_tokens": 0, "unknown_requests": 0,
              "reserved_unknown_tokens": 0, "requests": len(rows)}
    for row in rows:
        if row["status"] != "settled":
            result["unknown_requests"] += 1
            result["reserved_unknown_tokens"] += row["reserved"]
            continue
        usage = row["usage"]
        for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
            result[key] += usage.get(key, 0)
        result["handoff_tokens" if row["purpose"] == "handoff" else "main_tokens"] += row["charged"]
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def paired_summary(results):
    aggregate = {}
    for arm in ("summary", "boundary"):
        rows = [r for r in results if r["arm"] == arm]
        aggregate[arm] = {"attempted": len(rows), "passed": sum(r["passed"] for r in rows),
                          "tokens": sum(r["usage"]["total_tokens"] for r in rows),
                          "duplicate_reads": sum(r["duplicate_reads"] for r in rows),
                          "unknown_requests": sum(r["usage"]["unknown_requests"] for r in rows),
                          "eligible_recovery": sum(r["boundary_count"] > 0 for r in rows),
                          "recovered": sum(r["passed"] and r["boundary_count"] > 0 for r in rows)}
    base, new = aggregate["summary"], aggregate["boundary"]
    aggregate["token_reduction_percent"] = 100*(base["tokens"]-new["tokens"])/base["tokens"] if base["tokens"] else None
    return aggregate
