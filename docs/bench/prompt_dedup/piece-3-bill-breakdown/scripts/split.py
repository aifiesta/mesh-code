"""Print one run's bill, step by step, from the hop.usage events the engine
now sends, and check it against the billed totals.   python3 split.py RUN_DIR"""
import json, pathlib, sys
run = pathlib.Path(sys.argv[1])
ev = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines() if l.strip()]
i = next(k for k, e in enumerate(ev) if e["type"] == "turn.started"); sid = ev[i]["session"]
ev = [e for e in ev[i:] if e.get("session") == sid]
hops = [e["data"] for e in ev if e["type"] == "hop.usage"]
end = next(e["data"] for e in ev if e["type"] in ("turn.finished", "turn.aborted"))
P = ("fresh", "cached", "cache_write", "output")
total = lambda b: sum(b[p + "_usd"] for p in P) + b["other_usd"]
print(f"{'step':>4} {'tokens':>7} {'billed':>10} | {'fresh':>6} {'cached':>7} {'written':>7} {'output':>6} {'thinking':>8} | {'fresh $':>9} {'cached $':>9} {'written $':>9} {'output $':>9} | parts add up to")
for h in hops:
    b = h["bill"]
    print(f"{h['hop']:>4} {h['tokens']:>7} {h['cost']:>10.6f} | {b['fresh_tokens']:>6} {b['cached_tokens']:>7} {b['cache_write_tokens']:>7} {b['output_tokens']:>6} {b['thinking_tokens']:>8} | "
          f"{b['fresh_usd']:>9.6f} {b['cached_usd']:>9.6f} {b['cache_write_usd']:>9.6f} {b['output_usd']:>9.6f} | {total(b):.6f}{' (estimated)' if b['estimated'] else ''}")
b = end["bill"]; tok = sum(b[p + "_tokens"] for p in P); usd = total(b)
print(f"\nturn: {end['hops']} steps, {end['tokens']:,} tokens, billed ${end['cost']:.6f}, model {end['model']}")
print(f"check: tokens in the split {tok:,} (reported {end['tokens']:,}); dollars in the split ${usd:.6f} (billed ${end['cost']:.6f}); sum of the steps ${sum(h['cost'] for h in hops):.6f}; estimated: {b['estimated']}")
for p, label in zip(P, ("Fresh input", "Cached input", "Cache written", "Output")):
    print(f"  {label:14s} {b[p+'_tokens']:>8,} tokens ({100*b[p+'_tokens']/tok:4.1f}%)   ${b[p+'_usd']:.6f} ({100*b[p+'_usd']/usd:4.1f}% of the bill)")
print(f"  {'of which thinking':14s} {b['thinking_tokens']:>5,} tokens            ${b['thinking_usd']:.6f} ({100*b['thinking_usd']/usd:4.1f}% of the bill)")
