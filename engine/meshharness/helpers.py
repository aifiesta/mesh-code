"""Deep work helpers: the lead plans and reviews, cheap helpers write the pieces.

Only for a task that STARTS as Deep work under Smart routing. The lead was
Opus (the user's design, 24 Nov); since the evening of 24 Nov Deep work starts
on GPT-6.1 Sol at extra high and steps up to Opus only when Sol struggles, and
the helpers stay with the lead that starts the task (the user's call: "sol
extra high with the current helpers"). Below, "Opus" reads as "the lead".
Opus reads the code and plans as it always does; on
those turns it also has one more tool, `delegate`. If the work splits into
2-4 independent pieces on separate files and writing them is clearly the
bigger job, it hands each piece a short brief. Each piece goes to a fresh
DeepSeek V4.1 Flash helper (max thinking); all of them run at the same time
in the same project folder, each allowed to edit only its own files. When
they are done (or failed, or ran out of time or money) the tool returns
their reports, and Opus carries on in the same turn: reads the changes, runs
the full test suite, fixes what does not fit and writes the one summary.

Why this shape (feasibility test, 24 Nov, ~/Desktop/mesh-code-orchestration-test):
- It only paid under an expensive lead: Opus planning + reviewing cost a
  fairly fixed $0.17-0.23 a task, a DeepSeek helper about $0.01, against
  $0.34-0.90 for Opus doing it all. Under a cheap lead it always cost more.
- Whether a task is worth splitting is only known after reading the code,
  so Opus decides, mid-turn, by calling the tool or not.
- A helper waiting on another one deadlocked once; helpers run only their
  own tests, never wait, and have a time and a cost limit.
- The local model is not used: free, but 5-16 minutes a piece.

Helpers are ordinary `Session`s with no window of their own. Their spend is
added to the lead's turn as it happens (so the cost display and the turn's
total include it) and recorded in the daily ledger by the helper itself.
Their approval prompts, if the permission mode asks, show in the lead's
chat one at a time, and the click is passed on to the helper that asked.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .core import pricing as _pricing
from .core.tools import TOOLS
from .protocol import Event

# The helper model, best first; the first one the catalog lists is used.
HELPER_MODELS = ("deepseek/deepseek-v4.1-flash-1017", "deepseek/deepseek-v4.1-flash")
HELPER_PREFIX = "deepseek/deepseek-v4.1-flash"
HELPER_EFFORT = "max"
TIME_LIMIT = 10 * 60        # seconds per helper, wall clock
COST_LIMIT = 0.10           # dollars per helper (a piece cost $0.007-0.014 in the test)
STEP_LIMIT = 40             # model steps per helper
MIN_PIECES, MAX_PIECES = 2, 4
REPORT_CHARS = 1500
# A helper never asks the user, starts servers or writes repo notes: it has
# no chat of its own to answer in, and it is gone when its piece is done.
HELPER_SKIP_TOOLS = {"ask_user", "start_server", "remember"}

DELEGATE_TOOL = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Hand independent pieces of THIS change to cheaper helper models that write them "
            "at the same time, then come back to you. Only call it after you have read the code "
            "and planned, and only when ALL of these hold: (1) the work splits into 2-4 pieces "
            "that each edit DIFFERENT files (no file in two pieces); (2) writing the code is "
            "clearly the bigger part of the job, not a small fix; (3) each piece can be built "
            "from a short brief. Otherwise do the work yourself as usual. A helper step costs "
            "a small fraction (a twentieth or less) of one of yours, so when these hold, "
            "handing the writing (code "
            "and its tests) to helpers saves the user money; the review stays yours. Every file "
            "the request changes, README and docs included, belongs to one piece, so nothing "
            "is left for you to write. Each brief names the exact files the piece may edit "
            "(its test files "
            "too), the function names and signatures both sides rely on, the behaviour wanted, "
            "and which tests to run. Helpers read the files themselves: do not paste code or "
            "file contents into a brief. Helpers can't ask the user anything. The call returns "
            "each helper's report. Then YOU join: run the full test suite, read the changed "
            "code once, fix what is broken or missing from the request, finish any piece that "
            "failed, and give the user the final summary. You can call it once per task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pieces": {
                    "type": "array",
                    "description": "2-4 independent pieces, each on its own files.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string",
                                      "description": "A few words naming the piece."},
                            "files": {"type": "array", "items": {"type": "string"},
                                      "description": "Every file this piece may create or "
                                                     "edit, relative to the project folder."},
                            "brief": {"type": "string",
                                      "description": "What to build: files, functions and "
                                                     "signatures relied on, behaviour, which "
                                                     "tests to run. No pasted code."},
                        },
                        "required": ["title", "files", "brief"],
                    },
                },
            },
            "required": ["pieces"],
        },
    },
}

# Said to the lead with the user's message on a task where helpers are on:
# without it Opus read two files and started writing in 2 of 2 live runs, and
# the decision the flow is built on was never made. Hidden in the chat view.
DECIDE_NOTE = (
    "\n\n[Mesh Code: this is Deep work, so you can hand the writing to cheaper helpers "
    "with the `delegate` tool. Once you have read the code and know the change, decide "
    "before you write anything: if it splits into 2-4 independent pieces on separate files "
    "and writing them is the bigger part of the job, delegate them; if not, carry on "
    "yourself.]")
DECIDE_MARK = "\n\n[Mesh Code: this is Deep work"

# What the lead is told when the helpers are done. Kept short on purpose: a
# Sol lead that re-read every file, tests included, in small chunks and then
# polished passing code spent $0.20-0.55 on the review alone, more than the
# planning and all the helpers together (live, 24 Nov). Opus's quick reviews
# (run the suite, read the code once) cost $0.06 and missed nothing the
# hidden checks found.
REVIEW_ASK = (
    "The helpers have finished. Join their work now, in as few steps as you can:\n"
    "1. Run the full test suite once.\n"
    "2. Read the changed code files once, whole and in one go (one command or one read "
    "each). Do not read the test files unless a test fails, and do not read in small "
    "chunks.\n"
    "3. Fix only what is broken, or missing from the request, and finish any piece below "
    "that is not done. Do not tidy, restyle or extend code or tests that already pass.\n"
    "4. If you changed anything, run the full suite once more. Then give the user one "
    "final summary.")

HELPER_TOOLS = [t for t in TOOLS if t["function"]["name"] not in HELPER_SKIP_TOOLS]

HELPER_PROMPT = (
    "You are a helper on a bigger change. Other helpers are working IN THE SAME FOLDER AT "
    "THE SAME TIME on other files. Edit ONLY these files: {files}. Do ONLY your part, then "
    "reply with a 2-4 line report of what you changed. Run only the tests for YOUR files. "
    "Never wait, sleep or poll for another helper: if a test fails because of a file you do "
    "not own, leave it and say so in your report. The lead joins the parts afterwards.\n\n"
    "YOUR PART: {title}\n{brief}\n\n"
    "(For context only, the whole request: {task})"
)


def helper_model(catalog) -> str:
    ids = [str(m.get("id")) for m in (catalog or []) if isinstance(m, dict) and m.get("id")]
    for want in HELPER_MODELS:
        if want in ids:
            return want
    for i in ids:
        if i.startswith(HELPER_PREFIX):
            return i
    return HELPER_MODELS[0]


def norm_path(workspace: Path, path: str) -> str:
    """A file named in a brief or a tool call, as a path inside the project."""
    p = Path(str(path or "").strip()).expanduser()
    if not p.is_absolute():
        p = workspace / p
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def check_pieces(args: dict, workspace: Path) -> "tuple[list, str | None]":
    """The pieces as given, or an error the lead can act on."""
    pieces = args.get("pieces")
    if not isinstance(pieces, list) or not (MIN_PIECES <= len(pieces) <= MAX_PIECES):
        return [], (f"Error: delegate needs {MIN_PIECES}-{MAX_PIECES} pieces. If the work does "
                    "not split that way, do it yourself.")
    out, owner = [], {}
    for i, p in enumerate(pieces, 1):
        if not isinstance(p, dict):
            return [], f"Error: piece {i} must be an object with title, files and brief."
        files = [str(f).strip() for f in (p.get("files") or []) if str(f).strip()]
        brief = str(p.get("brief") or "").strip()
        if not files or not brief:
            return [], f"Error: piece {i} needs its files and a brief."
        for f in files:
            key = norm_path(workspace, f)
            if not key.startswith(str(workspace)):
                return [], f"Error: piece {i} names {f}, which is outside the project."
            if key in owner and owner[key] != i:
                return [], (f"Error: {f} is in piece {owner[key]} and piece {i}. Helpers run at "
                            "the same time, so each file may belong to one piece only. Re-split, "
                            "or do the work yourself.")
            owner[key] = i
        out.append({"n": i, "title": str(p.get("title") or f"Piece {i}").strip()[:80],
                    "files": files, "brief": brief})
    return out, None


class HelperRun:
    """Runs the pieces at once for one `delegate` call and reports back.

    Lives on the lead's worker thread for the length of the call. Helper
    sessions emit into `_on_event` from their own threads; everything shared
    goes through `self.lock`.
    """

    def __init__(self, lead, pieces: list, *, turn_id: str, call_id: str, task: str):
        self.lead, self.pieces = lead, pieces
        self.turn_id, self.call_id, self.task = turn_id, call_id, task
        self.lock = threading.Lock()
        self.approval_lock = threading.Lock()   # one helper approval card at a time
        self.model = helper_model(lead.catalog)
        ws = lead.workspace
        self.owner = {norm_path(ws, f): p["n"] for p in pieces for f in p["files"]}
        self.rows = [{"n": p["n"], "title": p["title"], "files": p["files"],
                      "status": "starting", "cost": 0.0, "steps": 0, "secs": 0.0,
                      "estimated": False} for p in pieces]
        self.reports = {p["n"]: "" for p in pieces}
        self.errors = {p["n"]: "" for p in pieces}
        self.ended = {p["n"]: False for p in pieces}
        self.started_at = {p["n"]: 0.0 for p in pieces}
        self.seen_start = {p["n"]: False for p in pieces}
        self.why: dict = {}                     # n -> why the lead stopped it
        self.sessions: dict = {}

    # -- what the lead's chat shows ------------------------------------------
    def publish(self) -> None:
        with self.lock:
            rows = [dict(r, cost=round(r["cost"], 6), secs=round(r["secs"], 1)) for r in self.rows]
        self.lead.emit(Event.HELPERS, turn_id=self.turn_id, call_id=self.call_id,
                       model=self.model, helpers=rows,
                       working=sum(1 for r in rows if r["status"] in ("starting", "working")),
                       cost=round(sum(r["cost"] for r in rows), 6))

    def _row(self, n: int) -> dict:
        return self.rows[n - 1]

    # -- events from one helper ----------------------------------------------
    def _on_event(self, n: int, ev: dict) -> None:
        typ, d = ev.get("type"), ev.get("data") or {}
        lead = self.lead
        if typ == Event.HOP_USAGE:
            cost = float(d.get("cost") or 0)
            with self.lock:
                row = self._row(n)
                row["cost"] += cost
                row["steps"] += 1
                row["estimated"] = row["estimated"] or bool(d.get("cost_estimated"))
                over = row["cost"] > COST_LIMIT and n not in self.why
                if over:
                    self.why[n] = "over its cost limit"
                lead.turn_cost += cost
                lead.turn_tokens += int(d.get("tokens") or 0)
                if d.get("cost_estimated"):
                    lead.turn_estimated = True
                if isinstance(d.get("bill"), dict):
                    _pricing.add_bill(lead.turn_bill, d["bill"])
            if over:
                # Stopped at once, before its next step is sent.
                self.sessions[n].interrupt()
            lead._status()
            self.publish()
        elif typ == Event.ASSISTANT_MESSAGE:
            self.reports[n] = str(d.get("text") or "")
        elif typ == Event.ERROR and d.get("fatal") is not False:
            self.errors[n] = str(d.get("message") or "")
        elif (typ == Event.NOTICE and d.get("level") == "warn"
              and str(d.get("message") or "").startswith("stopped")):
            self.errors[n] = str(d.get("message") or "")
        elif typ == Event.TURN_STARTED:
            self.seen_start[n] = True
        elif typ in (Event.TURN_FINISHED, Event.TURN_ABORTED):
            with self.lock:
                self.ended[n] = True
                self._row(n)["secs"] = time.time() - self.started_at[n]
            self.publish()
        elif typ == Event.TOOL_PROPOSED:
            # The user's permission mode asks for this call: the card shows in
            # the lead's chat, saying which helper is asking.
            lead.emit(Event.TOOL_PROPOSED, **dict(
                d, summary=f"Helper {n}: {d.get('summary') or d.get('name')}", helper=n))
        elif typ == Event.TOOL_DECIDED and not d.get("auto"):
            lead.emit(Event.TOOL_DECIDED, **dict(d, helper=n))
        elif typ == Event.FILE_CHANGED:
            lead.emit(Event.FILE_CHANGED, **d)

    # -- the run --------------------------------------------------------------
    def _make(self, piece: dict):
        from .session import Session          # here: session.py imports this module
        lead = self.lead
        cfg = dict(lead.cfg)
        cfg.update(model=self.model, route_mode="off", auto_route=False,
                   reasoning_effort=HELPER_EFFORT, repo_memory=False,
                   fallback_models=[], max_hops=STEP_LIMIT, stall_policy="pause")
        n = piece["n"]
        s = Session(f"{lead.id}-h{n}", lead.workspace, cfg,
                    lambda ev, n=n: self._on_event(n, ev),
                    title=f"helper {n}", mode=lead.mode, local=None, cap=lead.cap)
        s.catalog = lead.catalog
        s.helper = {"n": n, "files": {norm_path(lead.workspace, f) for f in piece["files"]},
                    "owner": self.owner, "approval_lock": self.approval_lock,
                    "names": piece["files"]}
        s.state["messages"] = s._system_messages()
        return s

    def run(self) -> str:
        """Start every helper, wait for all of them, return the report for
        the lead. Raises KeyboardInterrupt when the user stops the turn."""
        lead = self.lead
        for p in self.pieces:
            s = self._make(p)
            self.sessions[p["n"]] = s
            self.started_at[p["n"]] = time.time()
            self._row(p["n"])["status"] = "working"
            s.submit(HELPER_PROMPT.format(files=", ".join(p["files"]), title=p["title"],
                                          brief=p["brief"], task=self.task))
        # Approval clicks for a helper arrive at the lead (its chat shows the card).
        lead._helpers_live = list(self.sessions.values())
        self.publish()
        stopped_by_user = False
        why = self.why
        try:
            while True:
                if lead._interrupt.is_set():
                    stopped_by_user = True
                    for n, s in self.sessions.items():
                        if not self.ended[n]:
                            why[n] = "stopped"
                            s.interrupt()
                    self._wait_all(10)
                    break
                now = time.time()
                for n, s in self.sessions.items():
                    if (not self.ended[n] and self.seen_start[n]
                            and not s._busy.is_set()):
                        # The turn ended without its closing event (an
                        # exception the worker caught): it is over all the same.
                        self.ended[n] = True
                        self.errors[n] = self.errors[n] or "the helper stopped unexpectedly"
                    if self.ended[n] or n in why:
                        continue
                    row = self._row(n)
                    row["secs"] = now - self.started_at[n]
                    if row["secs"] > TIME_LIMIT:
                        why[n] = "timed out"
                        s.interrupt()
                if all(self.ended.values()):
                    break
                lead._interrupt.wait(0.25)
        finally:
            lead._helpers_live = []
            for n, s in self.sessions.items():
                row = self._row(n)
                if not self.ended[n]:
                    row["secs"] = time.time() - self.started_at[n]
                if n in why:
                    row["status"] = why[n]
                elif not self.ended[n]:
                    row["status"] = "failed"
                elif self.errors[n]:
                    row["status"] = "failed"
                else:
                    row["status"] = "done"
                # Files the helpers wrote count as this turn's, for the journal.
                lead.state.setdefault("_turn_files", []).extend(
                    s.state.get("_turn_files") or [])
                try:
                    s.close()
                except Exception:
                    pass
            self.publish()
        if stopped_by_user:
            raise KeyboardInterrupt
        return self.report()

    def _wait_all(self, timeout: float) -> None:
        end = time.time() + timeout
        while time.time() < end and not all(self.ended.values()):
            time.sleep(0.1)

    def report(self) -> str:
        lines = [REVIEW_ASK, ""]
        for p in self.pieces:
            n, row = p["n"], self._row(p["n"])
            head = (f"Piece {n} \"{p['title']}\" (files: {', '.join(p['files'])}): {row['status']} "
                    f"after {row['secs']:.0f} s, {row['steps']} steps, ${row['cost']:.4f}.")
            body = (self.reports[n] or "").strip()
            if len(body) > REPORT_CHARS:
                body = body[:REPORT_CHARS] + " …"
            if row["status"] != "done":
                hint = {"timed out": f"it hit the {TIME_LIMIT // 60}-minute limit",
                        "over its cost limit": f"it passed the ${COST_LIMIT:.2f} limit",
                        "stopped": "the user stopped it"}.get(row["status"], "it did not finish")
                err = f" Last error: {self.errors[n][:300]}" if self.errors[n] else ""
                body = (f"{body}\n" if body else "") + (
                    f"Not finished: {hint}.{err} Its files may hold partial edits: finish this "
                    "piece yourself.")
            lines.append(head)
            lines.append(f"Report: {body}" if body else "Report: (none)")
            lines.append("")
        return "\n".join(lines).rstrip()


def edit_refusal(session, path: str) -> "str | None":
    """For a helper: why it may not write `path`, or None. A helper may
    write its own files, and create a new file no other piece claims."""
    h = getattr(session, "helper", None)
    if not h:
        return None
    key = norm_path(session.workspace, path)
    if key in h["files"]:
        return None
    other = h["owner"].get(key)
    if other is None and not Path(key).exists():
        return None
    who = f"helper {other}'s" if other else "not one of your"
    return (f"Error: {path} is {who} files. You may edit only: {', '.join(h['names'])}. "
            "Leave it alone and say in your report what it needs; the lead joins the parts.")
