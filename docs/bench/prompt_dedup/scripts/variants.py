"""Build the prompt variants for the diagnosis: the old prompt with ONE of the
three removals applied at a time, plus the two controls (old, new).

    python3 variants.py

Each variant is a folder under diagnosis/variants/ with prompt.txt (the engine
part of the system prompt, {{CWD}} where the working directory goes) and
remember.txt (the description of the remember tool). The test window's engine
reads them when a chat starts."""
import importlib.util, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "diagnosis"

def load(name):
    spec = importlib.util.spec_from_file_location(name, D / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

CWD = "/__CWD__"
def paras(mod):
    text = mod.build_system_prompt({"repo_memory": False, "system": ""}, cwd=CWD)
    return text.replace(CWD, "{{CWD}}").split("\n\n")[1:]          # [0] is the empty base
def remember(mod):
    return next(t["function"]["description"] for t in mod.TOOLS if t["function"]["name"] == "remember")

old, new = load("tools_before"), load("tools_after")
P, Q = paras(old), paras(new)
find = lambda ps, start: next(i for i, p in enumerate(ps) if p.startswith(start))
MEM, SHELL, SERV = find(P, "MEMORY —"), find(P, "Shell commands run"), find(P, "For long-running servers")
short_servers = Q[find(Q, "BEFORE starting a long-running server")]
assert [p for i, p in enumerate(P) if i not in (MEM, SHELL, SERV)] == Q[:-1], "the two prompts differ somewhere unexpected"

variants = {
    "old":            (P, remember(old)),                                                   # control: must behave like the old engine
    "new":            (Q, remember(new)),                                                   # control: must behave like the changed engine
    "only-memory":    ([p for i, p in enumerate(P) if i != MEM], remember(new)),            # old prompt, memory paragraph removed
    "only-shell":     ([p for i, p in enumerate(P) if i != SHELL], remember(old)),          # old prompt, shell paragraph removed
    "only-servers":   ([short_servers if i == SERV else p for i, p in enumerate(P)], remember(old)),   # old prompt, servers paragraph cut down
}
extra = D / "extra_variants.py"                                     # variants added while narrowing down
if extra.exists():
    exec(extra.read_text())
for name, (ps, rem) in variants.items():
    d = D / "variants" / name; d.mkdir(parents=True, exist_ok=True)
    (d / "prompt.txt").write_text("\n\n".join(ps)); (d / "remember.txt").write_text(rem)
    print(f"{name:16s} prompt {len(chr(10).join(ps)) + len(ps) - 1:5d} chars, {len(ps)} paragraphs")
