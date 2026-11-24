"""The headless agent loop — one conversation, one workspace, no terminal.

This is the CLI's `cli.py` with the drawing taken out. The turn structure
is deliberately unchanged, because that structure is the part that works:

    submit a prompt
      -> route the turn (smart picker, local, zero tokens)
      -> hop: stream one model round trip, with retry/compaction recovery
      -> if the model asked for tools: classify, gate, execute, feed back
      -> loop until the model stops asking for tools, or a guard stops it
      -> settle cost and emit the turn summary

What changed is only the edges. `console.print(...)` became `emit(...)`.
`confirm_tool_call()` — which blocked on stdin — became a correlation id,
an event, and a parked thread waiting on `Pending`. `Path.cwd()` became
`self.workspace`, because one engine process now serves several windows.

Threading: each session owns one worker thread. Prompts queue onto it, so
a session processes one turn at a time while the transport stays responsive
(and other sessions keep running on their own threads). Everything the
worker touches that the transport also touches goes through `Pending` or an
atomic flag — there is no shared mutable state beyond that.
"""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx

from .bridge import Cancelled, Pending
from .core import compact, finishcheck, loopguard, memory, router, textcalls
from .core import journal, safety
from .core import pricing as _pricing
from .core import tools as tools_mod
from .core.client import (complete_chat, nearest_level, reasoning_refusal,
                          stream_chat)
from .core.permissions import AUTO_APPROVE, Mode
from .core.plan import Plan
from .core.tools import TOOLS, build_system_messages, find_stub_markers, summarize_call
from .local import prompt as local_prompt
from .local.manager import LocalUnavailable
from .local.provider import is_local
from .terminal import Terminal
from .protocol import Decision, Event, event

PLAN_TOOLS = {"create_plan", "update_step"}
# Batches of work with no `step` named before the engine says, once, that the
# checklist is standing still.
PLAN_QUIET_BATCHES = 3
INTERACTIVE_TOOLS = {"ask_user"}
UNGATED_TOOLS = PLAN_TOOLS | INTERACTIVE_TOOLS | {"remember"}

# How long a proposed tool call waits for a click before it is treated as a
# denial. Generous — a user may well walk away mid-turn — but not infinite,
# so a closed window can never strand a worker thread forever.
APPROVAL_TIMEOUT = 15 * 60
ASK_TIMEOUT = 30 * 60

_PORT_RANGE = (5173, 5199)

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
TOOL_SCHEMAS = {t["function"]["name"]: (t["function"].get("parameters") or {}).get("properties") or {}
                for t in TOOLS}
# Local model inside Smart routing (see the smart-routing section):
LOCAL_LEVELS = ("low", "mid")       # Lookup and Small change start local
LOCAL_STEP_LIMIT = 15               # steps on the local model before stepping up
LOCAL_FAILED_ROUNDS = 2             # failed tool rounds in a row before stepping up
# Tools that look at (or change) the project. A local answer with none of
# these behind it is a guess (seen live: a lookup answered in one step, wrong).
LOOK_TOOLS = {"read_file", "run_bash", "edit_file", "write_file"}


class Session:
    """One conversation against one workspace directory."""

    def __init__(self, session_id: str, workspace: str | Path, cfg: dict,
                 emit, *, title: str = "", mode: Mode = Mode.DEFAULT, local=None):
        self.id = session_id
        # The engine's LocalModels layer, or None (tests, a cloud-only engine).
        self.local = local
        # A step-up / back-to-local the user asked for from the UI thread;
        # the worker takes it at the start of the next hop.
        self._route_request: "tuple | None" = None
        self.workspace = Path(workspace).expanduser().resolve()
        self.title = title or self.workspace.name
        self._emit_raw = emit
        self.pending = Pending()

        self.cfg = dict(cfg)
        self.mode = mode
        self.created_at = time.time()
        # One project can hold several chats (sessions on the same
        # workspace). The label — the first thing the user asked — is how
        # the switcher tells them apart. last_active picks which chat a
        # plain "open this project" lands in.
        self.chat_label = ""
        self.last_active = time.time()
        self.session_cost = 0.0
        self.session_tokens = 0
        # Running totals for the turn IN FLIGHT. Session totals only move
        # when a turn ends, so without these the bar sits frozen through the
        # part of a run where you most want to see what it is costing.
        self.turn_model: str | None = None
        self.turn_local = False     # the last hop ran on this machine
        self.turn_cost = 0.0
        self.turn_tokens = 0
        self.turn_estimated = False
        # Where the money went: fresh input, cached input, cache writes,
        # output, thinking (see pricing.empty_bill). Same lifetimes as the
        # totals above — the turn's bill folds into the session's at turn end.
        self.turn_bill = _pricing.empty_bill()
        self.session_bill = _pricing.empty_bill()
        self.servers: list[dict] = []
        self.catalog: list | None = None
        # The user's own shell, created on first use. Not gated by permission
        # modes — this is them typing into their own terminal, which they
        # could do in Terminal.app regardless. The AGENT's tools are what
        # needs gating, not this.
        self._term: Terminal | None = None

        # The state dict the vendored engine modules expect. Keeping the
        # exact shape is what lets compact.py / memory.py / router.py run
        # here untouched.
        self.state: dict = {
            "cfg": self.cfg,
            "messages": [],
            "session_reads": {},
            "memory_root": self.workspace,
            "session_id": self.id,
            "plan": None,
            "stub_files": {},
            # Files whose content the model has been shown in this chat.
            # A failed edit may quote the file's real lines only for these.
            "read_paths": set(),
            "edit_misses": {},
            "session_allow": set(),
            "doom_streak": {},
            "mode": mode,
        }

        self._queue: queue.Queue = queue.Queue()
        self._interrupt = threading.Event()
        self._closing = threading.Event()
        self._busy = threading.Event()
        self._worker = threading.Thread(
            target=self._run, name=f"session-{self.id}", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------
    # emitting
    # ------------------------------------------------------------------
    def emit(self, type_: str, **data) -> None:
        try:
            self._emit_raw(event(type_, self.id, **data))
        except Exception:
            # A transport hiccup must never take down a turn mid-flight.
            pass

    def _status(self) -> None:
        self.emit(
            Event.STATUS,
            model=self.cfg.get("model"),
            mode=self.mode.value,
            route_mode=self.cfg.get("route_mode", "off"),
            route_effort=self.cfg.get("route_effort", "auto"),
            route_weights=self.cfg.get("route_weights"),
            reasoning_effort=self.cfg.get("reasoning_effort"),
            output_style=self.cfg.get("output_style", "default"),
            max_hops=self.cfg.get("max_hops", 0),
            stall_policy=self.cfg.get("stall_policy", "pause"),
            auto_compact=self.cfg.get("auto_compact", True),
            repo_memory=self.cfg.get("repo_memory", True),
            fallback_models=list(self.cfg.get("fallback_models") or []),
            exclude_models=list(self.cfg.get("exclude_models") or []),
            session_cost=round(self.session_cost, 6),
            session_tokens=self.session_tokens,
            # What is actually happening right now: the model the last hop
            # really ran on (which the router may have chosen, and which is
            # NOT cfg["model"]), and the spend so far this turn.
            turn_model=self.turn_model,
            turn_local=self.turn_local,
            turn_cost=round(self.turn_cost, 6),
            turn_tokens=self.turn_tokens,
            turn_estimated=self.turn_estimated,
            turn_bill=_pricing.round_bill(self.turn_bill),
            session_bill=_pricing.round_bill(self.session_bill),
            workspace=str(self.workspace),
            busy=self._busy.is_set(),
            servers=[{k: s.get(k) for k in ("pid", "port", "url", "cmd")}
                     for s in self.servers],
        )

    def describe(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "workspace": str(self.workspace),
            "model": self.cfg.get("model"),
            "mode": self.mode.value,
            "created_at": self.created_at,
            "label": self.chat_label,
            "session_cost": round(self.session_cost, 6),
            "session_tokens": self.session_tokens,
            "messages": len(self.state["messages"]),
            "busy": self._busy.is_set(),
        }

    # ------------------------------------------------------------------
    # public API — called from the transport thread
    # ------------------------------------------------------------------
    def _system_messages(self) -> list:
        """What a chat starts with: the stable system message, then this
        project's (see core.tools for why there are two)."""
        # The project journal rides in the system prompt so even a fresh
        # session (or a small model) opens knowing how this project runs
        # and what is already built — instead of rediscovering it by
        # re-doing it. Gated by the same privacy toggle as repo memory.
        block = ""
        if self.cfg.get("repo_memory", True):
            block = journal.prompt_block(self.workspace) or ""
        return build_system_messages(self.cfg, cwd=self.workspace, extra=block)

    def start(self) -> None:
        self.state["messages"] = self._system_messages()
        self.emit(
            Event.SESSION_STARTED,
            workspace=str(self.workspace),
            title=self.title,
            label=self.chat_label,
            created_at=self.created_at,
            model=self.cfg.get("model"),
            mode=self.mode.value,
            tools=[t["function"]["name"] for t in TOOLS],
        )
        self._status()

    def start_again(self) -> None:
        """Re-announce an existing session so a client can focus it.

        Deliberately does NOT reset messages, plan, or servers — reopening a
        project you already have open must land you back in the conversation,
        not on a blank one.
        """
        self.emit(
            Event.SESSION_STARTED,
            workspace=str(self.workspace),
            title=self.title,
            label=self.chat_label,
            created_at=self.created_at,
            model=self.cfg.get("model"),
            mode=self.mode.value,
            resumed=True,
            tools=[t["function"]["name"] for t in TOOLS],
        )
        self._status()

    def new_chat(self) -> bool:
        """Reset the CONVERSATION, keep the project: servers, terminal and
        the journal all survive. The old conversation is appended to the
        session transcript first, so it is archived rather than destroyed."""
        if self._busy.is_set():
            self.emit(Event.NOTICE, level="warn",
                      message="finish or stop the current turn before starting a new chat")
            return False
        try:
            from .core.config import append_transcript
            old = [m for m in self.state["messages"] if m.get("role") != "system"]
            if old:
                append_transcript(self.id, old)
        except Exception:
            pass
        self.state["messages"] = self._system_messages()
        self.chat_label = ""
        self.state["plan"] = None
        self.state["session_reads"] = {}
        self.state["stub_files"] = {}
        self.state["read_paths"] = set()
        self.state["edit_misses"] = {}
        self.emit(Event.CHAT_CLEARED)
        self._status()
        self.save_state()
        return True

    # -- persistence: the conversation must survive an app restart ------
    def save_state(self) -> None:
        """Snapshot this session to disk (best-effort, 0600)."""
        try:
            from .core import config as _cfg
            _dir = _cfg.SESSIONS_DIR
            _dir.mkdir(parents=True, exist_ok=True)
            import os as _os
            path = _dir / f"{self.id}.json"
            payload = json.dumps({
                "id": self.id,
                "title": self.title,
                "workspace": str(self.workspace),
                "created_at": self.created_at,
                "label": self.chat_label,
                "mode": self.mode.value,
                "model": self.cfg.get("model"),
                "session_cost": self.session_cost,
                "session_tokens": self.session_tokens,
                "session_bill": self.session_bill,
                "messages": self.state.get("messages") or [],
                "plan": self.state["plan"].to_dict() if self.state.get("plan") else None,
            })
            fd = _os.open(str(path), _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
            with _os.fdopen(fd, "w") as f:
                f.write(payload)
        except Exception:
            pass  # persistence must never break a turn

    @classmethod
    def restore(cls, data: dict, cfg: dict, emit, *, local=None) -> "Session":
        """Rebuild a session from its snapshot — conversation intact."""
        s = cls(data["id"], data["workspace"], cfg, emit,
                title=data.get("title") or "",
                mode=mode_from_str_safe(data.get("mode")), local=local)
        s.created_at = data.get("created_at") or s.created_at
        s.chat_label = data.get("label") or ""
        s.session_cost = float(data.get("session_cost") or 0)
        s.session_tokens = int(data.get("session_tokens") or 0)
        if isinstance(data.get("session_bill"), dict):
            # A chat saved before the split existed has none: its earlier
            # spend stays in the totals and is simply not broken down.
            _pricing.add_bill(s.session_bill, data["session_bill"])
        if data.get("model"):
            s.cfg["model"] = data["model"]
        msgs = data.get("messages") or []
        if msgs:
            s.state["messages"] = msgs
        else:
            s.state["messages"] = s._system_messages()
        try:
            if data.get("plan"):
                s.state["plan"] = Plan.from_dict(data["plan"])
        except Exception:
            pass
        return s

    def history_payload(self, cap: int = 400) -> list:
        """The conversation as the UI renders it: user/assistant text plus
        compact tool records. Built from engine history, so it survives
        restarts that the UI's own transcript did not."""
        out: list = []
        msgs = self.state.get("messages") or []
        results = {m.get("tool_call_id"): m.get("content")
                   for m in msgs if m.get("role") == "tool"}
        for m in msgs:
            role = m.get("role")
            if role == "user":
                content = m.get("content")
                if isinstance(content, list):
                    text = next((p.get("text") for p in content
                                 if isinstance(p, dict) and p.get("type") == "text"), "")
                else:
                    text = str(content or "")
                # Skip synthetic nudges (stall messages ride as user role)
                if text.strip():
                    out.append({"kind": "user", "text": text})
            elif role == "assistant":
                text = m.get("content")
                if isinstance(text, str) and text.strip():
                    out.append({"kind": "assistant", "text": text})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                        if not isinstance(args, dict):
                            args = {}
                    except (ValueError, TypeError):
                        args = {}
                    res = results.get(tc.get("id")) or ""
                    out.append({
                        "kind": "tool",
                        "name": fn.get("name") or "tool",
                        "summary": summarize_call(fn.get("name") or "tool", args),
                        "ok": not str(res).startswith("Error"),
                    })
            elif role == "system" and "summarized to save space" in str(m.get("content") or ""):
                out.append({"kind": "notice",
                            "text": "Earlier work summarized to save space."})
        return out[-cap:]

    def submit(self, text: str, attachments: list | None = None,
               refs: list | None = None) -> None:
        self.last_active = time.time()
        if not self.chat_label and (text or "").strip():
            self.chat_label = " ".join(text.split())[:60]
        # @-mentioned files ride as a footer the model sees and the UI does
        # not: the composer already shows them as badges, and the model
        # only needs to know which paths the user pointed at.
        paths = [str(r) for r in (refs or []) if isinstance(r, str) and r.strip()]
        if paths:
            listed = "\n".join(f"- {p}" for p in paths[:40])
            text = (f"{text.rstrip()}\n\n[Files the user referenced with @, "
                    f"relative to the working directory — read the ones you "
                    f"need before answering:\n{listed}]")
        self._queue.put(("prompt", text, attachments or []))

    def interrupt(self) -> None:
        """Abort the turn in flight. Safe to call when idle."""
        self._interrupt.set()
        # Release anything parked on a click — the turn is going away.
        self.pending.resolve_all()

    def respond_approval(self, token: str, decision: str) -> bool:
        return self.pending.resolve(token, decision)

    def respond_ask(self, token: str, answers) -> bool:
        return self.pending.resolve(token, answers)

    def terminal(self) -> Terminal:
        if self._term is None:
            self._term = Terminal(
                self.workspace,
                lambda **d: self.emit(Event.TERM_OUTPUT, **d),
            )
            self.emit(Event.TERM_READY, **self._term.start())
        return self._term

    def set_mode(self, mode: Mode) -> None:
        # Read live by the tool dispatch, so a change mid-turn applies to
        # the very next call — same contract the CLI's shift+tab had.
        self.mode = mode
        self.state["mode"] = mode
        self._status()

    def set_model(self, model: str) -> None:
        self.cfg["model"] = model
        self.state["cfg"] = self.cfg
        self._status()

    def update_cfg(self, **kw) -> None:
        self.cfg.update(kw)
        self.state["cfg"] = self.cfg
        self._status()

    def close(self) -> None:
        self._closing.set()
        self._interrupt.set()
        self.pending.resolve_all()
        self._queue.put(("stop", None, None))
        self.shutdown_servers()
        if self._term is not None:
            self._term.close()
        self.emit(Event.SESSION_CLOSED)

    # ------------------------------------------------------------------
    # worker
    # ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._closing.is_set():
            try:
                kind, text, attachments = self._queue.get()
            except Exception:
                break
            if kind == "stop":
                break
            # Re-check AFTER dequeue: close() may have fired between the top
            # of the loop and get() returning. Clearing _interrupt below would
            # otherwise erase the close's interrupt flag and run a whole turn
            # against a session whose servers/terminal were already torn down.
            if self._closing.is_set():
                break
            self._busy.set()
            self._interrupt.clear()
            try:
                self._run_turn(text, attachments)
            except Exception as e:      # never let a turn kill the session
                self.emit(Event.ERROR, message=f"{type(e).__name__}: {e}",
                          fatal=False)
            finally:
                self._busy.clear()
                self._status()

    # ------------------------------------------------------------------
    # one turn
    # ------------------------------------------------------------------
    def _run_turn(self, text: str, attachments: list) -> None:
        turn_id = uuid.uuid4().hex[:12]
        started = time.time()
        self.emit(Event.TURN_STARTED, turn_id=turn_id, text=text,
                  attachments=[a.get("name") for a in attachments])

        content = text
        if attachments:
            parts: list = [{"type": "text", "text": text}] if text else []
            for a in attachments:
                if a.get("data_url"):
                    parts.append({"type": "image_url",
                                  "image_url": {"url": a["data_url"]}})
            if parts:
                content = parts

        _retire_old_images(self.state["messages"])
        self.state["messages"].append({"role": "user", "content": content})
        self.state["stall"] = loopguard.StallDetector()
        self.state["stall_nudge_msg"] = None
        self.state["_compact_exhausted"] = False
        self.state["doom_streak"] = {}
        # Per-turn cascade counters (see the smart-routing section). Persist
        # across the turn but reset each turn: escalations climb from 0, clean
        # hops count fresh, the once-per-turn guards re-arm. `_smart_cohort`,
        # `_smart_last`, `_smart_bad` deliberately survive between turns —
        # continuation inheritance and session blacklists depend on them.
        self.state["_smart_escalations"] = 0
        self.state["_smart_clean_hops"] = 0
        self.state["_turn_tool_calls"] = 0
        self.state["_turn_files"] = []
        self.state["_turn_looked"] = 0
        self.state["_local_hops"] = 0
        self.state["_local_hops_total"] = 0
        self.state["_local_stepped_up"] = False
        self.state["_local_failed_rounds"] = 0
        self.state["_local_text_nudged"] = False
        self.state["_local_checked"] = False
        self.state.pop("_verify_fired", None)
        # Which turn this is, so a plan revised inside it can keep its ticks
        # while a plan made in a later turn starts clean (see _plan_tool).
        self.state["_turn_id"] = turn_id

        # Held on the session (not as locals) so _status() can publish the
        # running totals mid-turn. turn_model starts EMPTY on purpose: under
        # gateway routing the upstream picks, and we do not learn what ran
        # until the first hop answers. Claiming the pinned model before then
        # would be a guess presented as fact.
        self.turn_cost = 0.0
        self.turn_estimated = False
        self.turn_tokens = 0
        self.turn_model = None
        self.turn_local = False
        self.turn_bill = _pricing.empty_bill()

        # Smart routing resolves locally and stores its pick in
        # _smart_pick (NOT cfg["model"], which stays the user's pin). After
        # this the effective pick is known — _route_turn sets turn_model.
        self._route_turn(text, had_image=bool(attachments))

        model_used = self.cfg.get("model", "")
        self._status()
        hop = 0
        hop_limit = int(self.cfg.get("max_hops") or 0)
        aborted = False

        while True:
            hop += 1
            if self._interrupt.is_set():
                aborted = True
                break
            if hop_limit and hop > hop_limit:
                self.emit(Event.NOTICE, level="warn",
                          message=f"stopped after {hop_limit} hops (max_hops)")
                break

            self._take_route_request()
            if (self.state.get("_on_local")
                    and self.state.get("_local_hops", 0) >= LOCAL_STEP_LIMIT):
                self._smart_escalate(f"{LOCAL_STEP_LIMIT} steps on the local model")

            self.emit(Event.HOP_STARTED, turn_id=turn_id, hop=hop,
                      model=model_used)
            self._maybe_compact()

            extras = []
            nudge = self.state.pop("stall_nudge_msg", None)
            if nudge:
                extras = [{"role": "user", "content": nudge}]

            try:
                reply, meta = self._hop(extras)
            except KeyboardInterrupt:
                aborted = True
                break
            except httpx.HTTPStatusError as e:
                # A refused thinking level can arrive as a plain 4xx as well
                # as in-band; either way the turn should carry on.
                if (e.response.status_code < 500 and self._level_refused(
                        reasoning_refusal(_safe_text(e.response)))):
                    continue
                self.emit(Event.ERROR, fatal=True, message=(
                    f"gateway returned HTTP {e.response.status_code}"),
                    detail=_safe_text(e.response))
                break
            except httpx.RequestError as e:
                self.emit(Event.ERROR, fatal=True,
                          message=f"network error: {type(e).__name__}: {e}")
                break

            if meta.get("error"):
                msg = str(meta["error"])
                # A model that refuses this thinking level: remember it and
                # retry. (Same evidence-based rule as the CLI — never trust
                # the catalog's supports_thinking flag.)
                refusal = meta.get("reasoning_rejected")
                if refusal is None and "reasoning_effort" in msg:
                    refusal = reasoning_refusal(msg)
                if self._level_refused(refusal):
                    continue
                # If a SMART PICK errored, abandon it and retry on the pinned
                # model — the model answering now beats the table's opinion.
                # Only a pinned-model error (nothing to fall back to) is fatal.
                if self._smart_abandon("returned a gateway error"):
                    continue
                self.emit(Event.ERROR, fatal=True, message=msg)
                break

            model_used = meta.get("model") or model_used
            self.turn_model = model_used
            usage = meta.get("usage") or {}
            self.turn_tokens += int(usage.get("total_tokens") or 0)
            local_hop = bool(meta.get("local"))
            self.turn_local = local_hop
            if local_hop:
                # Free, and never priced against a catalog row: the bill split
                # shows the step at $0.
                self.state["_local_hops"] = self.state.get("_local_hops", 0) + 1
                self.state["_local_hops_total"] = self.state.get("_local_hops_total", 0) + 1
                hop_bill = _local_bill(usage)
                _pricing.add_bill(self.turn_bill, hop_bill)
                self.emit(Event.HOP_USAGE, turn_id=turn_id, hop=hop, model=model_used,
                          tokens=int(usage.get("total_tokens") or 0), cost=0.0,
                          cost_estimated=False, local=True,
                          bill=_pricing.round_bill(hop_bill))
                self._status()
                reply, meta = self._promote_text_calls(reply, meta, turn_id, hop)
                if meta.get("_nudged"):
                    continue
            else:
                # Billed figure by request id first (settles ~250ms after the
                # stream), then an in-body cost, then the catalog estimate.
                hop_cost, estimated = 0.0, False
                hop_bill, priced_as = None, self._effective_model()
                rids = meta.get("request_ids") or (
                    [meta["request_id"]] if meta.get("request_id") else [])
                if rids and usage:
                    try:
                        from .core import pricing
                        billed, hop_bill = pricing.fetch_request_bill(
                            self.cfg, rids, usage)
                    except Exception:
                        billed, hop_bill = None, None
                    if billed is not None:
                        hop_cost = float(billed)
                if hop_cost <= 0:
                    try:
                        hop_cost = float(meta.get("cost") or 0)
                    except (TypeError, ValueError):
                        hop_cost = 0.0
                if hop_cost <= 0 and usage:
                    try:
                        from .core import pricing
                        # Price the model we ASKED for first: that is the catalog
                        # id. The one the gateway echoes back is often the
                        # upstream's own name ("deepseek-flash" for
                        # deepseek/deepseek-v4.1-flash), which matched nothing and
                        # left 1.3M tokens showing as $0.0000.
                        guess = None
                        for candidate in (self._effective_model(),
                                          meta.get("model") or model_used):
                            if not candidate:
                                continue
                            guess = pricing.estimate_cost(candidate, usage, self.catalog)
                            if guess is not None:
                                priced_as = candidate
                                break
                        if guess:
                            hop_cost, estimated = float(guess), True
                    except Exception:
                        pass
                self.turn_cost += hop_cost
                if estimated:
                    self.turn_estimated = True
                # The split. From the gateway's own bill when it sent one;
                # otherwise worked out from the token counts and the catalog's
                # rates, scaled to this step's total, and marked estimated.
                if usage:
                    try:
                        if hop_bill is None:
                            hop_bill = _pricing.bill_from_usage(
                                priced_as or model_used, usage, self.catalog,
                                cost=hop_cost)
                        _pricing.add_bill(self.turn_bill, hop_bill)
                        self.emit(Event.HOP_USAGE, turn_id=turn_id, hop=hop,
                                  model=model_used,
                                  tokens=int(usage.get("total_tokens") or 0),
                                  cost=round(hop_cost, 8),
                                  cost_estimated=estimated,
                                  bill=_pricing.round_bill(hop_bill))
                    except Exception:
                        pass        # the split is reporting; it must never break a turn
            # Publish the running tally now, while the turn is still going.
            self._status()

            tool_calls = meta.get("tool_calls") or []
            has_reply = bool(reply and reply.strip())

            if not tool_calls:
                # Verify a no-tool answer before accepting it. A refusal, or a
                # claim of work with no tool call behind it, is a routing miss
                # the pick couldn't predict — escalate ONCE and let a stronger
                # model answer instead of shipping it.
                weak = router.verify_reply(
                    reply,
                    tool_calls_this_turn=self.state.get("_turn_tool_calls", 0),
                    user_asked_action=router.asks_for_action(text))
                if local_hop and self.state.get("_on_local"):
                    # The local model gets the same check every turn it answers
                    # (it does not use up the cloud pick's one), plus one more:
                    # an answer with nothing read behind it is a guess.
                    if not weak and not self.state.get("_turn_looked"):
                        weak = "it answered without reading any file"
                    if weak and self._smart_escalate(f"answer rejected ({weak})"):
                        continue  # the same hop again, on the cloud pick
                elif (weak and self.state.get("_smart_pick")
                        and not self.state.get("_verify_fired")):
                    self.state["_verify_fired"] = True
                    if self._smart_escalate(f"answer rejected ({weak})"):
                        continue  # retry the hop on the stronger model
                if has_reply:
                    self.emit(Event.ASSISTANT_MESSAGE, turn_id=turn_id, hop=hop,
                              text=reply, model=model_used)
                    self.state["messages"].append(
                        {"role": "assistant", "content": reply})
                if (local_hop and self.state.get("_on_local")
                        and self._finish_check_failed(turn_id, hop)):
                    continue      # the tests failed: the cloud pick takes it from here
                if has_reply:
                    self._plan_finish()
                break

            if has_reply:
                # WITH tool calls the text rides as the CONTENT of the single
                # tool_calls message (in _handle_tool_calls) rather than a
                # second consecutive assistant message — two in a row is what
                # strict Anthropic-translating gateways reject.
                self.emit(Event.ASSISTANT_MESSAGE, turn_id=turn_id, hop=hop,
                          text=reply, model=model_used)

            self.state["_turn_tool_calls"] = (
                self.state.get("_turn_tool_calls", 0) + len(tool_calls))
            report = self._handle_tool_calls(
                tool_calls, turn_id, hop, reply=reply if has_reply else None)
            if report.get("interrupted"):
                aborted = True
                break

            # A hop that ran real tools with no failure signal is CLEAN;
            # enough of them and the turn settles back down the effort ladder.
            # A doomed batch or fresh stub markers reset the streak and, for
            # the doom case, escalate — evidence the model is out of its depth.
            all_doomed = report["total"] > 0 and report["doomed"] == report["total"]
            if local_hop and self.state.get("_on_local"):
                failed_round = report["total"] > 0 and report["failed"] == report["total"]
                self.state["_local_failed_rounds"] = (
                    self.state.get("_local_failed_rounds", 0) + 1 if failed_round else 0)
                if self.state["_local_failed_rounds"] >= LOCAL_FAILED_ROUNDS:
                    self._smart_escalate(f"{LOCAL_FAILED_ROUNDS} failed tool rounds in a row")
            if report["doomed"] == 0 and not self.state.get("stub_files"):
                self.state["_smart_clean_hops"] = self.state.get("_smart_clean_hops", 0) + 1
                self._smart_de_escalate()
            else:
                self.state["_smart_clean_hops"] = 0
                if all_doomed:
                    self._smart_escalate("repeated malformed tool calls")

            action = self.state["stall"].observe(
                loopguard.batch_signature(tool_calls),
                all_doomed=all_doomed,
            )
            if action in ("nudge", "renudge"):
                # Repeating the same action is the clearest struggle signal —
                # escalate before nudging, so a stronger model gets the nudge.
                self._smart_escalate("model is repeating the same action")
                self.state["stall_nudge_msg"] = (
                    "You appear to be repeating the same action without "
                    "making progress. Stop and either try a materially "
                    "different approach or tell the user what is blocking you."
                )
                self.emit(Event.NOTICE, level="warn", message=(
                    "the model repeated the same action "
                    f"{self.state['stall'].last_cycles}× — nudging it"))
            elif action == "stop":
                if self.cfg.get("stall_policy") == "keep-going":
                    self.state["stall_nudge_msg"] = (
                        "You are still repeating the same action. Change "
                        "approach or explain what is blocking you.")
                    self.emit(Event.NOTICE, level="warn",
                              message="stall persists — continuing (stall policy: keep-going)")
                else:
                    self.emit(Event.NOTICE, level="warn", message=(
                        "stopped: the model repeated the same action "
                        f"{self.state['stall'].last_cycles} times in a row"))
                    break

        self.session_cost += self.turn_cost
        self.session_tokens += self.turn_tokens
        _pricing.add_bill(self.session_bill, self.turn_bill)
        turn_bill = _pricing.round_bill(self.turn_bill)
        elapsed = time.time() - started
        turn_cost, turn_tokens, turn_estimated = (
            self.turn_cost, self.turn_tokens, self.turn_estimated)

        if aborted:
            sealed = loopguard.seal_partial_batch(self.state["messages"])
            # Carry the spend on abort too. A turn stopped after real cost was
            # otherwise missing from the lifetime tally (Engine.emit only
            # counted TURN_FINISHED), and the UI had no per-turn figure for it.
            self.emit(Event.TURN_ABORTED, turn_id=turn_id, hops=hop,
                      sealed=sealed, elapsed=round(elapsed, 2),
                      model=model_used, cost=round(turn_cost, 6),
                      cost_estimated=turn_estimated, tokens=turn_tokens,
                      bill=turn_bill, local=self.turn_local,
                      local_steps=self.state.get("_local_hops_total", 0),
                      stepped_up_from_local=bool(self.state.get("_local_stepped_up")),
                      session_cost=round(self.session_cost, 6))
        else:
            self.emit(Event.TURN_FINISHED, turn_id=turn_id, hops=hop,
                      model=model_used, cost=round(turn_cost, 6),
                      cost_estimated=turn_estimated,
                      tokens=turn_tokens, elapsed=round(elapsed, 2),
                      bill=turn_bill, local=self.turn_local,
                      local_steps=self.state.get("_local_hops_total", 0),
                      stepped_up_from_local=bool(self.state.get("_local_stepped_up")),
                      session_cost=round(self.session_cost, 6),
                      plan=self.state["plan"].to_dict() if self.state["plan"] else None,
                      stubs=sorted(self.state.get("stub_files") or {}))
            # Fold this turn into the project journal so the NEXT session
            # (or model) starts from knowledge, not archaeology.
            if self.cfg.get("repo_memory", True):
                journal.record_turn(
                    self.workspace,
                    plan=self.state["plan"].to_dict() if self.state["plan"] else None,
                    servers=[{k: sv.get(k) for k in ("cmd", "url")} for sv in self.servers],
                    files_written=self.state.get("_turn_files") or [],
                    model=model_used, ok=True)

        # Fold complete: zero the in-flight fields so an idle STATUS (mode
        # change, a much-later start_again on reopen) can't rebroadcast this
        # dead turn's model/cost as if it were current.
        self.turn_cost = 0.0
        self.turn_tokens = 0
        self.turn_estimated = False
        self.turn_model = None
        self.turn_local = False
        self.turn_bill = _pricing.empty_bill()
        # A max_tokens shrink was derived from one over-long request's input.
        # Left set, it clips output for every later (now shorter) turn. Clear
        # it at turn end so the next turn re-derives from its own input.
        self.state.pop("_max_tokens_shrunk", None)

    # ------------------------------------------------------------------
    # one model round trip, with the CLI's retry ladder
    # ------------------------------------------------------------------
    def _hop(self, extras: list):
        attempt = 0
        empty_retries = 0
        compacted_for_context = False
        tried_non_streaming = False

        while True:
            attempt += 1
            msgs = self.state["messages"] + extras if extras else self.state["messages"]
            # Effective cfg: the LIVE smart pick as this hop's model (it may
            # have escalated since the turn began), reasoning stripped for
            # models that rejected it.
            cfg = self._effective_cfg()
            if self.state.get("_max_tokens_shrunk"):
                cfg["max_tokens"] = self.state["_max_tokens_shrunk"]
            target = None
            if self.state.get("_on_local"):
                target = self._local_target()
                if target is None:
                    self._smart_escalate("the local model is not loaded any more")
                else:
                    # Request-only: the stored chat keeps main's full prompt,
                    # so a step-up sends the cloud model everything as usual.
                    msgs = self._local_messages(msgs)
            elif is_local(cfg.get("model")):
                # The user picked a local model in the model list: every step
                # runs on it, as with any picked model. Loading it waits (and
                # Stop still stops); not downloaded, stopped or turned off
                # ends the turn with the reason.
                try:
                    target = self._picked_local_target(cfg["model"])
                except LocalUnavailable as e:
                    if self._interrupt.is_set():
                        raise KeyboardInterrupt
                    return "", {"error": f"local model: {e}", "local_unavailable": True}
                msgs = self._local_messages(msgs)
            picked_local = target is not None and not self.state.get("_on_local")
            try:
                if target is not None:
                    self.local.server.begin_use()
                try:
                    reply, meta = self._consume(stream_chat(msgs, cfg, tools=TOOLS, target=target))
                finally:
                    if target is not None:
                        self.local.server.end_use()
            except KeyboardInterrupt:
                raise
            except httpx.HTTPStatusError as e:
                if target is not None and not picked_local:
                    self._local_hop_failed(f"HTTP {e.response.status_code}")
                    attempt = 0
                    continue
                code = e.response.status_code
                body = _safe_text(e.response)
                overflow = loopguard.parse_max_tokens_overflow(body)
                if overflow is not None and not self.state.get("_max_tokens_shrunk"):
                    new = loopguard.adjusted_max_tokens(overflow)
                    if new:
                        self.state["_max_tokens_shrunk"] = new
                        self.emit(Event.NOTICE, level="info", message=(
                            "output budget exceeded the context window — "
                            f"retrying with max_tokens={new}"))
                        continue
                    if not compacted_for_context and self.cfg.get("auto_compact", True):
                        compacted_for_context = True
                        if self._compact_now("context limit reached", aggressive=True):
                            continue
                if (attempt < loopguard.MAX_STREAM_ATTEMPTS
                        and loopguard.is_retryable_status(code)):
                    self._wait_retry(attempt, f"gateway returned {code}",
                                     loopguard.retry_after_seconds(e.response))
                    continue
                if not tried_non_streaming and loopguard.is_retryable_status(code):
                    tried_non_streaming = True
                    got = self._non_streaming(msgs, target)
                    if got is not None:
                        return got
                raise
            except httpx.RequestError as e:
                if target is not None and not picked_local:
                    self._local_hop_failed(type(e).__name__)
                    attempt = 0
                    continue
                if picked_local:
                    # Picked local: no cloud to hand over to. Forget a dead
                    # server; the retry below then says why it cannot run.
                    try:
                        self.local.recover()
                    except Exception:
                        pass
                if attempt < loopguard.MAX_STREAM_ATTEMPTS:
                    self._wait_retry(attempt, f"network error ({type(e).__name__})")
                    continue
                if not tried_non_streaming:
                    tried_non_streaming = True
                    got = self._non_streaming(msgs, target)
                    if got is not None:
                        return got
                raise

            err = meta.get("error")
            if err and target is not None and not picked_local:
                self._local_hop_failed(str(err)[:120])
                attempt = 0
                continue
            if err:
                kind = loopguard.classify_inband_error(str(err))
                if kind == "transient" and attempt < loopguard.MAX_STREAM_ATTEMPTS:
                    self._wait_retry(attempt, "gateway is rate-limiting or overloaded")
                    continue
                if (kind == "context" and not compacted_for_context
                        and self.cfg.get("auto_compact", True)):
                    compacted_for_context = True
                    if self._compact_now("context limit hit", aggressive=True):
                        continue
                return reply, meta

            if (not meta.get("tool_calls") and not (reply or "").strip()
                    and attempt < loopguard.MAX_STREAM_ATTEMPTS
                    and empty_retries < 2):
                empty_retries += 1
                self._wait_retry(attempt, "empty response from the model")
                continue
            return reply, meta

    def _consume(self, events):
        """Drain a stream_chat generator into (text, meta), emitting deltas.

        The generator runs on a helper thread; this thread polls the queue
        every 100ms so an interrupt is seen even while the wire is silent.
        """
        import queue as _queue
        buf = []
        meta: dict = {}
        started = time.time()
        ttft = None
        q: "_queue.Queue" = _queue.Queue()
        _END = object()
        response_holder: dict = {}

        def _produce() -> None:
            try:
                for ev in events:
                    if isinstance(ev, dict) and "stream_response" in ev:
                        response_holder["r"] = ev["stream_response"]
                        continue
                    q.put(ev)
                q.put(_END)
            except BaseException as e:  # noqa: BLE001 — surface, never swallow
                q.put(e)

        t = threading.Thread(target=_produce, name="mesh-stream", daemon=True)
        t.start()

        while True:
            if self._interrupt.is_set():
                r = response_holder.get("r")
                if r is not None:
                    try:
                        r.close()
                    except Exception:
                        pass
                raise KeyboardInterrupt
            try:
                ev = q.get(timeout=0.1)
            except _queue.Empty:
                continue
            if ev is _END:
                break
            if isinstance(ev, BaseException):
                if isinstance(ev, (KeyboardInterrupt, GeneratorExit)):
                    raise KeyboardInterrupt
                # A response closed under httpx is the interrupt, not a fault.
                if self._interrupt.is_set():
                    raise KeyboardInterrupt
                raise ev
            if isinstance(ev, str):
                if ttft is None:
                    ttft = time.time() - started
                buf.append(ev)
                self.emit(Event.ASSISTANT_DELTA, text=ev)
            elif isinstance(ev, dict):
                if "stream_progress" in ev:
                    p = ev["stream_progress"] or {}
                    self.emit(Event.ASSISTANT_PROGRESS, tool=p.get("tool"),
                              chars=p.get("chars", 0))
                    continue
                if ev.get("stream_reset"):
                    # The marked attempt aborted in-band and the raw retry
                    # is starting over: drop its partial text so the stored
                    # reply isn't the two attempts concatenated.
                    buf.clear()
                    ttft = None
                    continue
                meta.update(ev)
        meta["elapsed"] = time.time() - started
        if ttft is not None:
            meta["ttft"] = ttft
        return "".join(buf), meta

    def _non_streaming(self, msgs, target=None):
        """Last-resort blocking request — Mesh's own retry and provider
        fallback only cover non-streaming calls, so this is where a run of
        streaming failures can still recover."""
        try:
            self.emit(Event.NOTICE, level="info",
                      message="streaming keeps failing — trying one non-streaming request")
            # The hop's effective cfg, as the streaming attempt used: the
            # live smart pick, and no thinking level this model has refused.
            return complete_chat(msgs, self._effective_cfg(), tools=TOOLS, target=target,
                                 max_tokens=self.state.get("_max_tokens_shrunk"))
        except Exception:
            return None

    def _wait_retry(self, attempt: int, reason: str, delay=None) -> None:
        wait = delay if delay is not None else loopguard.backoff_delay(attempt)
        self.emit(Event.NOTICE, level="warn",
                  message=f"{reason} — retrying in {wait:.1f}s",
                  attempt=attempt, wait=round(wait, 2))
        # Interruptible sleep: an abort should not have to outwait a backoff.
        self._interrupt.wait(timeout=wait)
        if self._interrupt.is_set():
            raise KeyboardInterrupt

    # ------------------------------------------------------------------
    # routing / compaction
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # smart routing — a within-turn cascade, not a one-shot pick
    #
    # Prediction picks the first model from the prompt; EVIDENCE picks the
    # next. The initial route reads difficulty from the message; then, per
    # hop, the same instrumentation the loop already runs (stall detector,
    # doom streak, stub markers, reply verification) escalates to a stronger
    # model when the current one is visibly struggling, and settles back down
    # after a run of clean hops. This is the single biggest reason the CLI
    # feels efficient: a hard task climbs to a frontier model only when it
    # needs to, and the boilerplate tail runs cheap again.
    #
    # `_smart_pick` holds the CURRENT hop's model. It is kept SEPARATE from
    # cfg["model"] (the user's pin) so the pin is never clobbered and the pick
    # can be abandoned back to it. None = ride the pinned model.
    # ------------------------------------------------------------------
    _CLEAN_HOPS_TO_SETTLE = 3
    _MAX_ESCALATIONS = 2

    def _effective_model(self) -> str:
        pick = self.state.get("_smart_pick")
        if pick and self.cfg.get("route_mode") == "smart":
            return pick
        return self.cfg.get("model", "")

    def _effective_cfg(self) -> dict:
        """cfg for THIS hop: the live smart pick as the model, minus settings
        this model has already proven it can't take."""
        cfg = dict(self.cfg)
        cfg["model"] = self._effective_model()
        # A scored smart pick chooses the thinking level along with the model.
        # A level the user set themselves always wins.
        if (self.state.get("_smart_scored") and self.state.get("_smart_pick")
                and self.cfg.get("route_mode") == "smart"
                and not self.cfg.get("reasoning_effort")):
            cfg["reasoning_effort"] = self.state.get("_smart_effort")
        # A level this model refused is swapped for the one chosen in its
        # place (which may itself have been refused since, hence the loop).
        swaps = self.state.get("_reasoning_swaps") or {}
        level, seen = cfg.get("reasoning_effort"), set()
        while level and (cfg["model"], level) in swaps and level not in seen:
            seen.add(level)
            level = swaps[(cfg["model"], level)]
        if level:
            cfg["reasoning_effort"] = level
        else:
            cfg.pop("reasoning_effort", None)
        return cfg

    def _level_refused(self, refusal: "dict | None") -> bool:
        """Handle a model refusing the thinking level it was just sent.

        Returns True when the hop should be retried. A model can refuse one
        level and take the rest (one refuses only "none"), so the memory is
        per model AND level, and the replacement is the nearest level the
        refusal says the model accepts — Max on a model that stops at High
        runs at High. Only when the error names no accepted level is the
        field dropped altogether.
        """
        if refusal is None:
            return False
        model = self._effective_model()
        sent = self._effective_cfg().get("reasoning_effort")
        if not sent:
            return False        # no level went out — this error is about something else
        swaps = self.state.setdefault("_reasoning_swaps", {})
        refused = {lv for (m, lv) in swaps if m == model} | {sent}
        instead = nearest_level(sent, refusal.get("accepted"), refused)
        swaps[(model, sent)] = instead
        self.emit(Event.NOTICE, level="info", message=(
            f"this model does not take the '{sent}' reasoning level — "
            + (f"using '{instead}' instead" if instead else "retrying without it")))
        return True

    def _switch_cost_bonus(self) -> float:
        """Stickiness bonus for the incumbent, scaled by cached context.
        Switching re-sends the whole history and throws away the prompt cache
        — cheap early in a turn, a real bill 40k tokens in."""
        try:
            tok = compact.est_history_tokens(self.state.get("messages") or [])
        except Exception:
            return 0.0
        return min(12.0, tok / 2500.0)

    def _needs_ctx(self) -> int:
        return int(compact.est_history_tokens(self.state.get("messages") or []) * 1.3) + 4096

    def _apply_pick(self, info: dict, difficulty: str) -> None:
        info["difficulty"] = difficulty
        self.state["_smart_difficulty"] = difficulty
        self.state["_smart_pick"] = info["model"]
        self.state["_smart_last"] = info["model"]
        self.state["_smart_pick_info"] = info
        # Scored picks carry a thinking level; table picks choose a model only.
        self.state["_smart_scored"] = bool(info.get("scored"))
        self.state["_smart_level"] = info.get("level") if info.get("scored") else None
        self.state["_smart_effort"] = info.get("effort") if info.get("scored") else None
        self.turn_model = info["model"]

    def _serving(self) -> tuple:
        """(model, thinking level) of the pick serving right now."""
        return (self.state.get("_smart_pick"), self.state.get("_smart_level"))

    def _route_turn(self, text: str, had_image: bool = False) -> None:
        """Initial per-turn pick. Fail-open: any miss leaves _smart_pick unset
        and the pinned model rides. Then, for Lookup and Small change, the
        local model when it is loaded: the cloud pick stays in _smart_pick as
        the model a step-up goes to."""
        self.state["_smart_pick"] = None
        self.state["_on_local"] = False
        self.state["_turn_level"] = None
        self.state["_turn_cohort"] = None
        if self.cfg.get("route_mode") != "smart":
            return
        self._route_cloud(text, had_image)
        try:
            self._start_local(had_image)
        except Exception:
            self.state["_on_local"] = False   # never let routing break a turn

    def _route_cloud(self, text: str, had_image: bool) -> None:
        try:
            table = router.load_table()
            history_chars = sum(
                len(m.get("content") or "") if isinstance(m.get("content"), str) else 0
                for m in self.state.get("messages") or [])
            cohort, conf = router.classify(
                text, has_image=had_image, has_tools=True,
                history_chars=history_chars)
            # Short follow-ups ("yes", "2", "continue") are answers WITHIN the
            # ongoing task, not new tasks — inherit its cohort/difficulty
            # rather than reclassifying three characters. Judged by shape, not
            # a bare length rule.
            inherit = bool(
                self.state.get("_smart_cohort") and not had_image
                and (router.is_continuation(text)
                     or (conf <= 0.5 and len(text.strip()) < 25)))
            if inherit:
                cohort = self.state["_smart_cohort"]
            forced = self.cfg.get("route_effort", "auto")
            why = None
            if forced != "auto":
                difficulty, why = forced, "you set it in Settings"
            elif inherit:
                difficulty = self.state.get("_smart_difficulty") or "mid"
                why = "a follow-up to the task before it"
            elif cohort in router.SCORED_COHORTS:
                # Coding work is sorted by the shape of the request, and
                # wording never assigns the top two rungs.
                difficulty, why = router.task_level(text)
            else:
                difficulty = router.estimate_difficulty(text)
            self.state["_turn_level"] = difficulty
            self.state["_turn_cohort"] = cohort
            # The level is known even with no catalog (offline): the local
            # model can still take the turn.
            if not table or not self.catalog:
                return
            self.state["_smart_cohort"] = cohort
            self.state["_smart_base_difficulty"] = difficulty   # de-escalation floor
            exclude = set(self.cfg.get("exclude_models") or []) | set(self.state.get("_smart_bad") or set())
            got = router.choose(cohort, difficulty, self.cfg.get("route_weights"),
                                table, self.catalog,
                                needs_tools=True, needs_ctx=self._needs_ctx(),
                                incumbent=self.state.get("_smart_last"),
                                exclude=exclude,
                                level=self.cfg.get("reasoning_effort"))
            if got and got.get("model"):
                self._apply_pick(got, difficulty)
                self.emit(Event.ROUTED, model=got["model"], cohort=cohort,
                          confidence=conf, difficulty=difficulty,
                          thinking=got.get("level"),
                          task=_task_name(got, difficulty), why=why,
                          ranked=(got.get("ranked") or [])[:5])
        except Exception:
            self.state["_smart_pick"] = None  # never let routing break a turn

    def _smart_escalate(self, reason: str, *, explicit: bool = False) -> bool:
        """The current model is visibly struggling — climb the effort ladder
        for the rest of this turn and re-pick. Bounded to _MAX_ESCALATIONS so
        a hard task reaches a frontier model but a broken one can't spend
        forever. `explicit` is the user's Escalate button: no cap applies.

        On the local model, the first step is off it, to Smart routing's own
        pick for this task level (not "something clearly stronger", which
        from a local model would jump past the cheap pick to a dear one)."""
        if self.state.get("_on_local"):
            return self._step_off_local(reason)
        if self.cfg.get("route_mode") != "smart":
            return False
        if not explicit and self.state.get("_smart_escalations", 0) >= self._MAX_ESCALATIONS:
            return False
        if not explicit and self.cfg.get("route_effort", "auto") != "auto":
            return False  # user pinned the effort — respect it
        try:
            table = router.load_table()
            if not table or not self.catalog:
                return False
            cohort = self.state.get("_smart_cohort") or "chat"
            cur = self.state.get("_smart_difficulty") or "mid"
            nxt = router.escalate(cur)
            if nxt == cur:
                return False  # already at the top
            prev = self._serving()
            got = router.choose(cohort, nxt, self.cfg.get("route_weights"),
                                table, self.catalog,
                                needs_tools=True, needs_ctx=self._needs_ctx(),
                                incumbent=None,  # escalating: no stickiness
                                exclude=set(self.state.get("_smart_bad") or set()),
                                # only something clearly stronger than what just struggled
                                failed=prev if self.state.get("_smart_scored") else None,
                                level=self.cfg.get("reasoning_effort"))
            if not got or not got.get("model"):
                return False
            self.state["_smart_escalations"] = self.state.get("_smart_escalations", 0) + 1
            self.state["_smart_clean_hops"] = 0
            got["escalated"] = True
            self._apply_pick(got, nxt)
            if self._serving() != prev:
                self.emit(Event.ROUTED, model=got["model"], cohort=cohort,
                          difficulty=nxt, escalated=True, reason=reason,
                          thinking=got.get("level"), task=_task_name(got, nxt),
                          ranked=(got.get("ranked") or [])[:5])
                self.emit(Event.NOTICE, level="info",
                          message=f"escalating ({reason}) → {_pick_label(got)}")
            return True
        except Exception:
            return False

    def _smart_de_escalate(self) -> None:
        """After a run of clean hops, settle back down the effort ladder so a
        single escalation doesn't tax the whole rest of the turn at frontier
        prices. Never below the turn's original predicted difficulty."""
        if self.cfg.get("route_mode") != "smart" or not self.state.get("_smart_escalations"):
            return
        if self.cfg.get("route_effort", "auto") != "auto":
            return
        if self.state.get("_smart_clean_hops", 0) < self._CLEAN_HOPS_TO_SETTLE:
            return
        floor = self.state.get("_smart_base_difficulty") or "low"
        cur = self.state.get("_smart_difficulty") or "mid"
        nxt = router.de_escalate(cur, floor=floor)
        if nxt == cur:
            return
        try:
            table = router.load_table()
            if not table or not self.catalog:
                return
            cohort = self.state.get("_smart_cohort") or "chat"
            got = router.choose(cohort, nxt, self.cfg.get("route_weights"),
                                table, self.catalog,
                                needs_tools=True, needs_ctx=self._needs_ctx(),
                                incumbent=self.state.get("_smart_last"),
                                exclude=set(self.state.get("_smart_bad") or set()),
                                incumbent_bonus=self._switch_cost_bonus(),
                                level=self.cfg.get("reasoning_effort"))
            if not got or not got.get("model"):
                return
            prev = self._serving()
            self.state["_smart_escalations"] = max(0, self.state.get("_smart_escalations", 1) - 1)
            self.state["_smart_clean_hops"] = 0
            self._apply_pick(got, nxt)
            if self._serving() != prev:
                self.emit(Event.ROUTED, model=got["model"], cohort=cohort,
                          difficulty=nxt, settled=True, thinking=got.get("level"),
                          task=_task_name(got, nxt),
                          ranked=(got.get("ranked") or [])[:5])
                self.emit(Event.NOTICE, level="info",
                          message=f"settling → {_pick_label(got)}")
        except Exception:
            pass

    def _smart_abandon(self, reason: str) -> bool:
        """The smart pick failed LIVE (empty replies / fatal). Blacklist it
        for the session and fall back to the pinned model — outcome beats the
        table's opinion. True if there was a pick to abandon."""
        bad = self.state.get("_smart_pick")
        if not bad:
            return False
        self.state.setdefault("_smart_bad", set()).add(bad)
        self.state["_smart_pick"] = None
        self.state["_smart_last"] = None
        self.turn_model = self.cfg.get("model")
        self.emit(Event.NOTICE, level="warn", message=(
            f"smart pick {_short_model(bad)} {reason} — falling back to "
            f"{_short_model(self.cfg.get('model',''))} (skipped for this session)"))
        return True

    # ------------------------------------------------------------------
    # the local model, as one more choice inside Smart routing
    #
    # Lookup and Small change start on it once it is downloaded and loaded;
    # everything else, and any turn where it is not ready, is routed exactly
    # as before. `_on_local` says the next hop goes to it; `_smart_pick`
    # keeps Smart routing's own pick for the level, which is where a step-up
    # lands. After a step-up the turn stays on the cloud; the next message
    # starts local again.
    # ------------------------------------------------------------------
    def _local_target(self):
        """A Target for the loaded local model, or None (never blocks)."""
        if self.local is None or not self.cfg.get("local_enabled", True):
            return None
        try:
            return self.local.ready_target()
        except Exception:
            return None

    def _picked_local_target(self, model_id: str):
        """A Target for a local model the user picked in the model list.
        Blocks while it loads; raises LocalUnavailable with a reason."""
        if self.local is None or not self.cfg.get("local_enabled", True):
            raise LocalUnavailable("local models are turned off in Settings", permanent=True)
        return self.local.target_for(
            model_id, cancel=self._interrupt,
            notify=lambda msg: self.emit(Event.NOTICE, level="info", message=msg))

    def _local_fit(self) -> "tuple[bool, str]":
        """Does this chat fit the local model? Half its window at most: the
        rest is the task's own reading and writing."""
        window = self.local.context_window() if self.local is not None else None
        if not window:
            return False, "the local model has no window yet"
        used = compact.est_history_tokens(self._local_messages(self.state.get("messages") or []))
        if used > window // 2:
            return False, (f"this chat is about {used // 1000}k tokens, more than half the "
                           f"local model's {window // 1024}k window")
        return True, ""

    def _start_local(self, had_image: bool) -> None:
        level, cohort = self.state.get("_turn_level"), self.state.get("_turn_cohort")
        if (had_image or not level or router._band(level) not in LOCAL_LEVELS
                or cohort not in router.SCORED_COHORTS):
            return
        target = self._local_target()
        if target is None or not self._local_fit()[0]:
            return
        self._go_local(target, why="free on this machine")

    def _go_local(self, target, *, why: str, back: bool = False) -> None:
        self.state["_on_local"] = True
        self.state["_local_hops"] = 0
        self.state["_local_failed_rounds"] = 0
        self.turn_model = target.display_id
        level = self.state.get("_turn_level")
        self.emit(Event.ROUTED, model=target.display_id, local=True,
                  cohort=self.state.get("_turn_cohort"), difficulty=level,
                  task=router.LEVEL_NAMES.get(router._band(level)) if level else None,
                  why=why, thinking=None, settled=back, ranked=[])

    def _step_off_local(self, reason: str) -> bool:
        if not self.state.get("_on_local"):
            return False
        self.state["_on_local"] = False
        self.state["_local_stepped_up"] = True
        model = self._effective_model()
        info = self.state.get("_smart_pick_info") if self.state.get("_smart_pick") else None
        label = _pick_label(info) if info else _short_model(model)
        self.turn_model = model
        level = self.state.get("_turn_level")
        self.emit(Event.ROUTED, model=model, escalated=True, from_local=True, reason=reason,
                  cohort=self.state.get("_turn_cohort"), difficulty=level,
                  thinking=self.state.get("_smart_level"),
                  task=router.LEVEL_NAMES.get(router._band(level)) if level else None,
                  ranked=((info or {}).get("ranked") or [])[:5])
        self.emit(Event.NOTICE, level="info",
                  message=f"stepping up from the local model ({reason}) → {label}")
        return True

    def _local_hop_failed(self, what: str) -> None:
        """The local server failed on a hop: the cloud pick takes the same
        hop. Not retried locally, and never restarted behind the user."""
        try:
            self.local.recover()
        except Exception:
            pass
        self._smart_escalate(f"the local server failed: {what}")

    def _local_messages(self, msgs: list) -> list:
        """The chat as a small local model is sent it: one short system
        prompt in place of main's two system messages (a Qwen chat template
        takes one system message, first), later system notes (a compaction
        summary) as user notes, and text only. Built per request; the stored
        chat is never changed."""
        block = journal.prompt_block(self.workspace) if self.cfg.get("repo_memory", True) else ""
        out = [{"role": "system", "content": local_prompt.build_compact_system_prompt(
            self.cfg, cwd=str(self.workspace), journal_block=block or "")}]
        i = 0
        while i < len(msgs) and msgs[i].get("role") == "system":
            i += 1
        for m in msgs[i:]:
            content = m.get("content")
            if isinstance(content, list):
                content = "\n".join(p.get("text") or "" for p in content
                                    if isinstance(p, dict) and p.get("type") == "text")
            if m.get("role") == "system":
                out.append({"role": "user", "content": f"[Note from Mesh Code]\n{content or ''}"})
                continue
            m = {k: v for k, v in m.items() if k != "cache_control"}
            if content is not m.get("content"):
                m["content"] = content
            out.append(m)
        return out

    def _promote_text_calls(self, reply: str, meta: dict, turn_id: str, hop: int):
        """A small model may print a tool call as text instead of calling it.
        The common shapes become real calls; anything else gets one nudge a
        turn. Local hops only: a cloud reply quoting a call is text."""
        if meta.get("tool_calls") or not textcalls.looks_like_tool_call(reply or ""):
            return reply, meta
        # The turn id keeps these ids unique across turns: a cloud model that
        # takes the chat over may refuse two tool calls with the same id.
        clean, calls, _why = textcalls.extract(reply, TOOL_NAMES, schemas=TOOL_SCHEMAS,
                                               id_prefix=f"text_{turn_id}_{hop}")
        if calls:
            for c in calls:
                self.emit(Event.TOOL_REPAIRED, call_id=c["id"], name=c["name"], kind="text_parsed")
            return clean, {**meta, "tool_calls": calls}
        if not self.state.get("_local_text_nudged"):
            self.state["_local_text_nudged"] = True
            self.state["messages"].append({"role": "user", "content": (
                "You printed a tool call as text instead of calling the function:\n\n"
                + (reply or "")[:2000]
                + "\n\nCall the function instead; do not write <tool_call> or "
                  "<function=...> in your reply.")})
            return reply, {**meta, "_nudged": True}
        return reply, meta

    def _finish_check_failed(self, turn_id: str, hop: int) -> bool:
        """The local model says a change is done: run the project's tests,
        through the same approval as any command. True when they failed and
        the turn stepped up to the cloud pick with the output in hand."""
        if self.state.get("_local_checked") or not self.state.get("_turn_files"):
            return False
        self.state["_local_checked"] = True
        cmd = finishcheck.find_test_command(self.workspace)
        if not cmd:
            return False
        self.emit(Event.NOTICE, level="info",
                  message="checking the local model's change: running the project's tests")
        p = {"id": f"check_{turn_id}_{hop}", "name": "run_bash",
             "args": {"command": cmd, "timeout": 180}, "kind": "ok"}
        try:
            result = self._execute_one(p, turn_id, hop)
        except KeyboardInterrupt:
            raise
        except Exception:
            return False
        if finishcheck.verdict(result) is not False:
            return False
        self.state["messages"].append({"role": "user", "content": (
            f"[Mesh Code ran the project's tests after that change (`{cmd}`) and they fail:\n"
            f"{_clip(result, 3000)}\n\nFix the code so the tests pass.]")})
        return self._smart_escalate("the project's tests fail after its change")

    def _take_route_request(self) -> None:
        """Apply an Escalate / Back to local the user asked for meanwhile."""
        req, self._route_request = self._route_request, None
        if not req:
            return
        kind, harder = req
        if kind == "escalate":
            why = "you asked for a stronger model"
            if self.state.get("_on_local"):
                self._step_off_local(why)
                if harder:
                    self._smart_escalate(why, explicit=True)
            else:
                self._smart_escalate(why, explicit=True)
                if harder:
                    self._smart_escalate(why, explicit=True)
        elif kind == "local" and not self.state.get("_on_local"):
            ok, why = self._local_ready_for_back()
            target = self._local_target() if ok else None
            if target is None:
                self.emit(Event.NOTICE, level="warn", message=f"staying on the cloud model: {why or 'the local model is not loaded'}")
                return
            self._go_local(target, why="you asked for the local model", back=True)
            self.emit(Event.NOTICE, level="info", message="back to the local model")

    def _local_ready_for_back(self) -> "tuple[bool, str]":
        if self.local is None or not self.cfg.get("local_enabled", True):
            return False, "local models are turned off in Settings"
        ok, why = self._local_fit()
        if not ok:
            return False, why
        if self._local_target() is None:
            return False, "the local model is not loaded yet (loading it now)"
        return True, ""

    # -- called from the transport thread ------------------------------
    def escalate(self, *, harder: bool = False) -> None:
        """The Escalate button: off the local model to Smart routing's pick
        for this level; `harder` goes one step further, to what Smart routing
        would step up to from that pick. Idle: applies to the next message."""
        self._route_request = ("escalate", bool(harder))
        if not self._busy.is_set():
            self.emit(Event.NOTICE, level="info",
                      message="the next message starts on " + ("a stronger cloud model" if harder
                                                                else "Smart routing's cloud pick"))

    def deescalate(self) -> None:
        """Back to local. Refused, with the reason, when the chat is over
        half the local window or the local model is not ready."""
        ok, why = self._local_ready_for_back()
        if not ok:
            self.emit(Event.NOTICE, level="warn", message=f"can't go back to the local model: {why}")
            return
        self._route_request = ("local", False)
        if not self._busy.is_set():
            self.emit(Event.NOTICE, level="info", message="the next message starts on the local model")

    def explain_route(self, text: str) -> dict:
        """What would smart routing pick for this prompt, and why?

        The CLI answered this with `/route why` after the fact. Answering it
        BEFORE the turn is what makes routing legible rather than magic: you
        see the cohort, the candidates and the prices you are choosing
        between, while you can still change your mind.
        """
        out = {"available": False, "cohort": None, "ranked": [],
               "mode": self.cfg.get("route_mode", "off")}
        try:
            # Classify FIRST. Cohort and difficulty are computed locally from
            # the prompt alone — they cost nothing and are worth showing even
            # when the catalog has not arrived and no pick is possible yet.
            cohort, confidence = router.classify(text or "", has_tools=True)
            level = self.cfg.get("route_effort", "auto")
            why = None
            if cohort in router.SCORED_COHORTS:
                difficulty, why = router.task_level(text or "")
            else:
                difficulty = router.estimate_difficulty(text or "")
            weights = router.effective_weights(
                self.cfg.get("route_weights"),
                difficulty if level == "auto" else level)
            out.update({"cohort": cohort, "confidence": confidence,
                        "difficulty": difficulty, "weights": weights})

            table = router.load_table()
            if not table or not self.catalog:
                out["reason"] = ("the model catalog has not loaded yet"
                                 if table else "no routing table is bundled")
                return out
            got = router.choose(cohort, difficulty if level == "auto" else level,
                                self.cfg.get("route_weights"), table, self.catalog,
                                needs_tools=True, incumbent=self.cfg.get("model"),
                                exclude=set(self.cfg.get("exclude_models") or []),
                                level=self.cfg.get("reasoning_effort"))
            out.update({
                "available": bool(got),
                "pick": (got or {}).get("model"),
                "thinking": (got or {}).get("level"),
                "task": _task_name(got or {}, difficulty if level == "auto" else level),
                "why": why if level == "auto" else "you set it in Settings",
                "ranked": ((got or {}).get("ranked") or [])[:8],
            })
            if not got:
                excluded = len(self.cfg.get("exclude_models") or [])
                out["reason"] = (
                    "no model on your account is rated for this kind of work"
                    + (f" once the {excluded} you excluded are removed" if excluded else "")
                )
        except Exception as e:
            out["reason"] = f"{type(e).__name__}: {e}"
        return out

    def _maybe_compact(self) -> None:
        if self.state.get("_on_local") and self.local is not None:
            # A small model works worse from a summary, and the cloud pick has
            # room for the whole chat: at the point where this window would
            # compact, step up instead. (The point itself is unchanged.)
            window = self.local.context_window()
            if window and compact.should_compact(self._local_messages(self.state["messages"]), window):
                self._smart_escalate("the chat outgrew the local model's window")
        if not self.cfg.get("auto_compact", True):
            return
        if self.state.get("_compact_exhausted"):
            return
        # Use the EFFECTIVE model's window — a mid-turn escalation may have
        # moved to a model with a different context limit.
        limit = compact.context_limit(self._effective_model(), self.catalog)
        if compact.should_compact(self.state["messages"], limit):
            # Pass the limit so the FOLD phase is reachable. Without it,
            # compact_history only truncates; the second call finds nothing
            # left to truncate, returns {}, and _compact_exhausted trips —
            # making the next context error fatal. The whole fold machinery
            # was dead before this argument was threaded through.
            self._compact_now("approaching the context limit", limit=limit)

    def _compact_now(self, why: str, *, limit: "int | None" = None,
                     aggressive: bool = False) -> bool:
        try:
            report = compact.compact_history(
                self.state, limit=limit, aggressive=aggressive)
        except Exception:
            return False
        if not report:
            self.state["_compact_exhausted"] = True
            return False
        try:
            memory.invalidate_dropped(self.state)
        except Exception:
            pass
        self.emit(Event.COMPACTED, reason=why, **{
            k: report.get(k) for k in ("before_tok", "after_tok", "truncated", "folded")})
        return True

    # ------------------------------------------------------------------
    # tool dispatch
    # ------------------------------------------------------------------
    def _handle_tool_calls(self, tool_calls: list, turn_id: str, hop: int,
                           reply: "str | None" = None) -> dict:
        from .core.tools import repair_tool_args, validate_call  # noqa: F401
        prepared = [_prepare_call(tc) for tc in tool_calls]

        self.state["messages"].append({
            "role": "assistant",
            "content": reply or None,
            "tool_calls": [
                {"id": p["id"], "type": "function",
                 "function": {"name": p["name"], "arguments": p["history_args"]}}
                for p in prepared],
        })
        self.state["_batch_assistant_idx"] = len(self.state["messages"]) - 1

        doomed = failed = 0
        worked = stepped = False
        for p in prepared:
            if self._interrupt.is_set():
                return {"total": len(prepared), "doomed": doomed, "failed": failed + doomed,
                        "interrupted": True}

            name, args = p["name"], p["args"]
            # `step` is the model telling the ENGINE which plan step this
            # call belongs to. It is not the tool's argument: taken off here,
            # before anything validates, previews or runs the call.
            step = args.pop("step", None) if isinstance(args, dict) else None

            if (p["kind"] == "invalid" and name == "edit_file" and step is not None
                    and isinstance(args.get("old_text"), str) and args.get("old_text")
                    and args.get("old_text") == args.get("new_text")):
                # An edit that changes nothing, carrying a step number: a
                # model announcing "I am on step N" the only way it found.
                # It is not a broken call — counting it as one made the
                # router step up to a model 10x the price (seen live). Take
                # the step, say there is nothing to announce, move on.
                worked = True
                stepped = self._plan_follow(step) or stepped
                result = ("Nothing changed: old_text and new_text are the same. "
                          "There is no need to announce a plan step — just do "
                          "the work, with `step` on the calls that do it.")
                self.emit(Event.TOOL_SKIPPED, call_id=p["id"], name=name,
                          kind="noop", reason=result.split(".")[0] + ".",
                          turn_id=turn_id, hop=hop)
                self._append_tool_result(p["id"], result)
                continue

            if p["kind"] in ("invalid", "truncated", "unparseable") and name not in PLAN_TOOLS:
                doomed += 1
                streaks = self.state.setdefault("doom_streak", {})
                streaks[name] = streaks.get(name, 0) + 1
                result = _doom_feedback(p, streaks[name])
                self.emit(Event.TOOL_SKIPPED, call_id=p["id"], name=name,
                          kind=p["kind"], reason=result.splitlines()[0],
                          turn_id=turn_id, hop=hop)
                self._append_tool_result(p["id"], result)
                continue

            if p["kind"] in ("repaired", "normalized"):
                self.emit(Event.TOOL_REPAIRED, call_id=p["id"], name=name,
                          kind=p["kind"])

            if name not in UNGATED_TOOLS:
                worked = True
                stepped = self._plan_follow(step) or stepped

            try:
                result = self._execute_one(p, turn_id, hop)
            except KeyboardInterrupt:
                self._append_tool_result(p["id"], "Interrupted by the user.")
                return {"total": len(prepared), "doomed": doomed, "failed": failed + doomed,
                        "interrupted": True}
            except Exception as e:
                result = f"Error: tool execution raised {type(e).__name__}: {e}"
                self.emit(Event.TOOL_RESULT, call_id=p["id"], name=name,
                          ok=False, result=result)

            if str(result).startswith("Error"):
                failed += 1
            elif name in LOOK_TOOLS and not str(result).startswith("User denied"):
                self.state["_turn_looked"] = self.state.get("_turn_looked", 0) + 1
            self._append_tool_result(p["id"], result)

        if worked:
            self._plan_nudge(stepped)
        return {"total": len(prepared), "doomed": doomed, "failed": failed + doomed,
                "interrupted": False}

    def _append_tool_result(self, call_id: str, result: str) -> None:
        self.state["messages"].append(
            {"role": "tool", "tool_call_id": call_id, "content": result})

    def _execute_one(self, p: dict, turn_id: str, hop: int) -> str:
        name, args = p["name"], p["args"]

        # --- ungated: bookkeeping and interaction, no filesystem or shell
        if name in PLAN_TOOLS:
            return self._plan_tool(name, args)
        if name in INTERACTIVE_TOOLS:
            return self._ask_user(args, turn_id)
        if name == "remember":
            note = memory.append_note(self.state["memory_root"], args.get("note") or "")
            # Learnings are the journal's core entries — the PM notebook.
            if self.cfg.get("repo_memory", True):
                journal.note(self.workspace, args.get("note") or "")
            self.emit(Event.MEMORY_NOTE, note=args.get("note") or "", result=note)
            return note

        self.state.setdefault("doom_streak", {}).pop(name, None)

        # An edit that cannot apply (no match, several matches, no such
        # file) is answered before anyone is asked: "approve this?" for a
        # call that can only fail is a wasted click.
        if name == "edit_file":
            doomed = self._edit_precheck(args)
            if doomed is not None:
                self.emit(Event.TOOL_STARTED, call_id=p["id"], name=name,
                          summary=summarize_call(name, args),
                          args=_safe_args(name, args), turn_id=turn_id, hop=hop)
                self.emit(Event.TOOL_RESULT, call_id=p["id"], name=name,
                          ok=False, result=doomed)
                return doomed

        # --- gated: mode set, then the safety guards, then the user
        mode = self.mode          # live read, so a mid-batch change applies
        approved = name in AUTO_APPROVE.get(mode, set())
        safety_mode = mode
        # "Always allow" is the user's own click for this tool, so it is
        # checked at the looser, pre-existing strictness; a call approved
        # only by the MODE gets the stricter project-only rule for reads.
        always = name in self.state.get("session_allow", set())
        if not approved and always:
            approved, safety_mode = True, Mode.AUTO

        reason = ""
        if approved:
            ok, why = self._safety_check(name, args, safety_mode, always=always)
            if not ok:
                approved, reason = False, why or "safety check failed"
                self.emit(Event.NOTICE, level="warn",
                          message=f"auto-approval blocked: {reason}")

        if not approved:
            decision = self._request_approval(p, turn_id, hop, reason)
            if decision == Decision.ALWAYS:
                self.state.setdefault("session_allow", set()).add(name)
                approved = True
            else:
                approved = decision == Decision.ALLOW
            self.emit(Event.TOOL_DECIDED, call_id=p["id"], name=name,
                      decision=decision, auto=False)
        else:
            self.emit(Event.TOOL_DECIDED, call_id=p["id"], name=name,
                      decision=Decision.ALLOW, auto=True)

        if not approved:
            if decision == Decision.INTERRUPTED:
                return ("Error: interrupted by the user before this call ran. "
                        "It was not executed — re-run it if still needed.")
            return "User denied this tool call."

        self.emit(Event.TOOL_STARTED, call_id=p["id"], name=name,
                  summary=summarize_call(name, args), args=_safe_args(name, args),
                  turn_id=turn_id, hop=hop)

        if name == "start_server":
            result = self._start_server(args, p["id"])
            self.emit(Event.TOOL_RESULT, call_id=p["id"], name=name,
                      ok=not result.startswith("Error:"), result=_clip(result))
            return result
        if name == "write_file":
            return self._write_file(args, p["id"])
        if name == "edit_file":
            return self._edit_file(args, p["id"])
        if name == "read_file":
            return self._read_file(args, p["id"])

        result = tools_mod.execute(name, args, self.cfg, cwd=self.workspace,
                                   cancel=self._interrupt)
        self.emit(Event.TOOL_RESULT, call_id=p["id"], name=name,
                  ok=not result.startswith("Error:"), result=_clip(result))
        if self._interrupt.is_set() and result.startswith("Error: interrupted"):
            raise KeyboardInterrupt
        return result

    def _safety_check(self, name: str, args: dict, mode: Mode,
                      always: bool = False):
        if name in ("write_file", "edit_file"):
            return safety.is_path_safe_for_auto_write(
                args.get("path"), mode, self.workspace)
        if name == "read_file":
            # Approved by the mode alone (accept-edits, auto): only inside
            # the project, never an environment file. Bypass and the user's
            # own "Always allow" keep the denylist-only rule they had.
            scoped = not always and mode in (Mode.ACCEPT_EDITS, Mode.AUTO)
            # The engine's own command logs count as readable alongside the
            # project: a trimmed command result points the model at one.
            return safety.is_path_safe_for_auto_read(
                args.get("path"), mode, self.workspace, workspace_only=scoped,
                extra_roots=(tools_mod.shell_log_dir(),))
        if name in ("run_bash", "start_server"):
            return safety.is_command_safe_for_auto(
                args.get("command"), mode, self.workspace)
        return True, None

    def _request_approval(self, p: dict, turn_id: str, hop: int, reason: str) -> str:
        """Emit the proposal and park this thread until the UI answers.

        The correlation id is reserved BEFORE the event goes out, so a very
        fast click cannot arrive before there is a slot to receive it.
        """
        token = self.pending.open()
        name, args = p["name"], p["args"]
        self.emit(Event.TOOL_PROPOSED, token=token, call_id=p["id"], name=name,
                  summary=summarize_call(name, args), args=_safe_args(name, args),
                  preview=self._preview(name, args), blocked_reason=reason,
                  turn_id=turn_id, hop=hop, mode=self.mode.value)
        try:
            return str(self.pending.wait(token, timeout=APPROVAL_TIMEOUT))
        except Cancelled:
            # Interrupted, disconnected, or timed out. Denial is the only
            # safe reading of "no answer" for a tool that writes or shells.
            # But if the CAUSE was the user interrupting the turn (not a
            # deliberate "no" on this call), say so — recording "user denied"
            # would tell the next turn's model the user refused this specific
            # action, which it did not.
            if self._interrupt.is_set():
                return Decision.INTERRUPTED
            return Decision.DENY

    def _preview(self, name: str, args: dict) -> dict:
        """What the approval dialog shows above the buttons."""
        if name == "write_file":
            path = args.get("path") or ""
            content = args.get("content") or ""
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = self.workspace / target
            old = ""
            try:
                old = target.read_text()
            except Exception:
                pass
            return {"kind": "diff", "path": str(target), "exists": bool(old),
                    "old": _clip(old, 20000), "new": _clip(content, 20000),
                    "bytes": len(content)}
        if name == "edit_file":
            return self._edit_preview(args)
        if name in ("run_bash", "start_server"):
            return {"kind": "command", "command": args.get("command") or "",
                    "cwd": str(self.workspace)}
        if name == "read_file":
            path = args.get("path") or ""
            if args.get("offset") is not None or args.get("limit") is not None:
                # "read_file: a.py (lines 120-199)" -> "a.py (lines 120-199)"
                path = summarize_call(name, args).split(": ", 1)[-1]
            return {"kind": "path", "path": path}
        if name == "web_search":
            return {"kind": "query", "query": args.get("query") or ""}
        return {"kind": "json", "value": _safe_args(name, args)}

    # ------------------------------------------------------------------
    # individual tools that need harness-specific handling
    # ------------------------------------------------------------------
    def _write_file(self, args: dict, call_id: str) -> str:
        path, content = args.get("path") or "", args.get("content") or ""
        result = tools_mod.execute("write_file", args, self.cfg, cwd=self.workspace)
        ok = not result.startswith("Error:")
        self.emit(Event.TOOL_RESULT, call_id=call_id, name="write_file",
                  ok=ok, result=result)
        if ok:
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = self.workspace / target
            key = str(target)
            try:
                stubs = self.state.setdefault("stub_files", {})
                found = find_stub_markers(key, content)
                if found:
                    stubs[key] = found
                    self.emit(Event.NOTICE, level="warn", message=(
                        f"{target.name} looks like scaffolding, not finished code"),
                        detail=found[:5], path=key)
                else:
                    stubs.pop(key, None)
            except Exception:
                pass
            try:
                # Memory resolves relative paths against the ENGINE cwd, not
                # the workspace — pass the already-workspace-resolved absolute
                # path (`key`) so repo memory and the read-dedupe keys line up
                # with where the file actually is. Bare relatives made capture
                # silently record nothing and dedupe never fire.
                memory.capture(self.state["memory_root"], key, content)
                memory.record_write(self.state, key, content,
                                    msg_index=self.state.get("_batch_assistant_idx", 0))
            except Exception:
                pass
            self.state.setdefault("_turn_files", []).append(key)
            self._mark_shown(target)
            self.emit(Event.FILE_CHANGED, path=key, bytes=len(content),
                      action="write")
        return result

    # -- targeted edits ---------------------------------------------------
    def _target(self, path: str) -> Path:
        target = Path(path or "").expanduser()
        return target if target.is_absolute() else self.workspace / target

    def _mark_shown(self, target: Path) -> None:
        try:
            self.state.setdefault("read_paths", set()).add(str(target.resolve()))
        except (OSError, RuntimeError):
            pass

    def _was_shown(self, target: Path) -> bool:
        try:
            return str(target.resolve()) in self.state.get("read_paths", set())
        except (OSError, RuntimeError):
            return False

    def _plan_edit(self, args: dict) -> dict:
        target = self._target(args.get("path"))
        return tools_mod.plan_edit(
            target, args.get("old_text") or "", args.get("new_text") or "",
            bool(args.get("replace_all")), quote=self._was_shown(target),
            shown=args.get("path"))

    def _edit_miss_note(self, target: Path, message: str) -> str:
        """Count consecutive misses on one file; from the second, say to
        look again instead of guessing a third time."""
        misses = self.state.setdefault("edit_misses", {})
        key = str(target)
        misses[key] = misses.get(key, 0) + 1
        if misses[key] >= 2:
            message += ("\nThat is " + str(misses[key]) + " failed edits of this "
                        "file in a row. Stop guessing: read the lines you "
                        "want to change again (a small range is enough), "
                        "then copy old_text from that result.")
        return message

    def _edit_precheck(self, args: dict) -> "str | None":
        """The error a doomed edit would produce, or None to carry on.

        Runs only where a read would be approved without asking (inside the
        project, not an environment or credential file). Anywhere else the
        call goes to the user first: even "0 matches" tells the model
        something about a file nobody agreed to open."""
        ok, _ = safety.is_path_safe_for_auto_read(
            args.get("path"), Mode.AUTO, self.workspace, workspace_only=True)
        if not ok:
            return None
        plan = self._plan_edit(args)
        if "error" not in plan:
            return None
        return self._edit_miss_note(self._target(args.get("path")), plan["error"])

    def _edit_preview(self, args: dict) -> dict:
        """The changed lines with a little context, numbered as in the file."""
        target = self._target(args.get("path"))
        old, new = args.get("old_text") or "", args.get("new_text") or ""
        plan = tools_mod.plan_edit(target, old, new, bool(args.get("replace_all")))
        if "error" in plan:
            # Outside the pre-check's reach (a path that needs approval just
            # to look at). Show what was asked for; the result will explain.
            return {"kind": "diff", "path": str(target), "exists": True,
                    "old": _clip(old, 20000), "new": _clip(new, 20000),
                    "bytes": len(new), "start_line": 1, "note": ""}
        context = 3
        first = plan["line"]
        start = max(1, first - context)
        before, after = plan["before"].split("\n"), plan["after"].split("\n")
        old_end = first + old.count("\n") + context
        new_end = first + new.count("\n") + context
        extra = plan["count"] - 1
        note = (f"and {extra} more identical replacement"
                f"{'s' if extra != 1 else ''} elsewhere in the file"
                if extra > 0 else "")
        return {"kind": "diff", "path": str(target), "exists": True,
                "old": _clip("\n".join(before[start - 1:old_end]), 20000),
                "new": _clip("\n".join(after[start - 1:new_end]), 20000),
                "bytes": len(plan["after"]), "start_line": start, "note": note}

    def _edit_file(self, args: dict, call_id: str) -> str:
        target = self._target(args.get("path"))
        new_text = args.get("new_text") or ""
        result, plan = tools_mod.apply_edit(
            target, args.get("old_text") or "", new_text,
            bool(args.get("replace_all")), quote=self._was_shown(target),
            shown=args.get("path"))
        ok = plan is not None
        if not ok and result.startswith("Error: old_text"):
            result = self._edit_miss_note(target, result)
        self.emit(Event.TOOL_RESULT, call_id=call_id, name="edit_file",
                  ok=ok, result=result)
        if not ok:
            return result
        key = str(target)
        content = plan["after"]
        self.state.setdefault("edit_misses", {}).pop(key, None)
        try:
            # The quality guard looks at what the model just wrote, placed
            # at its real line numbers — not at the whole file, or a TODO
            # that was already there would be blamed on a one-line edit.
            stubs = self.state.setdefault("stub_files", {})
            found = find_stub_markers(
                key, "\n" * (plan["line"] - 1) + new_text)
            if found:
                stubs[key] = found
                self.emit(Event.NOTICE, level="warn", message=(
                    f"{target.name} looks like scaffolding, not finished code"),
                    detail=found[:5], path=key)
            elif key in stubs:
                # Flagged earlier in this turn: is it clean now?
                still = find_stub_markers(key, content)
                if still:
                    stubs[key] = still
                else:
                    stubs.pop(key, None)
        except Exception:
            pass
        try:
            memory.capture(self.state["memory_root"], key, content)
            # Only the replaced lines are in the conversation, so nothing
            # may claim the file's full content is.
            memory.forget_read(self.state, key)
        except Exception:
            pass
        self.state.setdefault("_turn_files", []).append(key)
        self._mark_shown(target)
        self.emit(Event.FILE_CHANGED, path=key, bytes=len(content),
                  action="edit")
        return result

    def _read_file(self, args: dict, call_id: str) -> str:
        path = args.get("path") or ""
        # Same reason as _write_file: hand memory the workspace-resolved path.
        abspath = Path(path).expanduser()
        if not abspath.is_absolute():
            abspath = self.workspace / abspath
        abspath = str(abspath)
        # A line range is a small, deliberate request for specific lines —
        # answer it as asked. Only whole-file reads are deduped.
        ranged = args.get("offset") is not None or args.get("limit") is not None
        stub = None
        if not ranged:
            try:
                stub = memory.dedupe_read(self.state, abspath)
            except Exception:
                stub = None
        if stub is not None:
            self.emit(Event.TOOL_RESULT, call_id=call_id, name="read_file",
                      ok=True, result=stub, deduped=True)
            return stub
        detail = tools_mod.read_file_detail(args, cwd=self.workspace)
        result = detail["text"]
        ok = not result.startswith("Error:")
        self.emit(Event.TOOL_RESULT, call_id=call_id, name="read_file",
                  ok=ok, result=_clip(result), bytes=len(result))
        if ok and detail["shown"]:
            self._mark_shown(Path(abspath))
        if ok and detail["whole"]:
            # ONLY a whole-file result is recorded: the dedupe stub tells the
            # model "the full content is already in this conversation", which
            # is false after a line range, a first chunk, or a generated-file
            # note.
            try:
                memory.record_read(self.state, abspath, result,
                                   msg_index=len(self.state["messages"]))
                memory.capture(self.state["memory_root"], abspath, result)
            except Exception:
                pass
        return result

    def _plan_tool(self, name: str, args: dict) -> str:
        if name == "create_plan":
            steps = args.get("steps")
            if not isinstance(steps, list) or not steps:
                return "Error: create_plan requires a non-empty `steps` list."
            # Ticks carry over only when the model REVISES a plan it made in
            # this same turn. A plan made in a later turn is a new task: a step
            # that shares a title with a finished one ("Run the tests") has
            # not been done again, and pre-ticking it let the turn end with
            # the checklist, and the project journal, claiming work nobody did.
            prev = self.state.get("plan")
            turn = self.state.get("_turn_id")
            same_turn = (prev is not None and turn is not None
                         and self.state.get("_plan_turn") == turn)
            plan = Plan(steps, previous=prev if same_turn else None)
            if not plan.steps:
                return "Error: all steps were empty after trimming whitespace."
            plan.begin()
            self.state["plan"] = plan
            self.state["_plan_turn"] = turn
            self.state["_plan_nudged"] = False
            self.state["_plan_quiet"] = 0
            self.emit(Event.PLAN_UPDATED, **plan.to_dict())
            now = plan.current()
            # No "now call ..." here: the old wording asked for a tick and
            # got a round trip that did nothing else.
            return (f"Plan created with {len(plan.steps)} step(s)"
                    + (f"; step {now[0]} is in progress" if now else "")
                    + ". Start the work now.")

        if name == "update_step":
            plan = self.state.get("plan")
            if plan is None:
                return "Error: no active plan. Call create_plan first."
            err = plan.update(args.get("index"), args.get("status"))
            if err:
                return f"Error: {err}"
            self.emit(Event.PLAN_UPDATED, **plan.to_dict())
            # A retired tool (old chats still call it from habit). Honour
            # the call, and say how it works now.
            return (f"Step {args['index']} → {args['status']}. {plan.summary()} "
                    "Steps are ticked for you now: put `step` on your tool "
                    "calls instead of calling this.")

        return f"Error: unknown plan tool `{name}`"

    # -- the engine keeps the checklist ---------------------------------
    def _plan_follow(self, step) -> bool:
        """A work call carried `step`. True when it named a real step."""
        plan = self.state.get("plan")
        if plan is None:
            return False
        changed = plan.follow(step)
        if changed:
            self.emit(Event.PLAN_UPDATED, **plan.to_dict())
        return changed is not None

    def _plan_nudge(self, stepped: bool) -> None:
        """Once per plan: several batches of work went by and none named a
        step, so the checklist may be standing still. Said at the point of
        action (on the result the model reads next), because advice in the
        prompt is what cheap models skip. Step 1 needs no number, so the
        first batches after a plan are expected to be quiet."""
        plan = self.state.get("plan")
        if plan is None or plan.is_complete():
            return
        if stepped:
            self.state["_plan_quiet"] = 0
            return
        self.state["_plan_quiet"] = self.state.get("_plan_quiet", 0) + 1
        if self.state["_plan_quiet"] < PLAN_QUIET_BATCHES or self.state.get("_plan_nudged"):
            return
        now = plan.current()
        last = self.state["messages"][-1] if self.state["messages"] else None
        if not now or len(plan.steps) < 2 or not last or last.get("role") != "tool" \
                or not isinstance(last.get("content"), str):
            return
        self.state["_plan_nudged"] = True
        last["content"] += (
            f"\n\n[plan] The checklist still shows step {now[0]} of "
            f"{len(plan.steps)} ({now[1].title}). If you are past it, put the "
            "current step's number in `step` on your next tool call.")

    def _plan_finish(self) -> None:
        """The turn ended with an answer: tick the last step if it was the
        one in progress."""
        plan = self.state.get("plan")
        if plan is not None and plan.finish():
            self.emit(Event.PLAN_UPDATED, **plan.to_dict())

    def _ask_user(self, args: dict, turn_id: str) -> str:
        """The model's mid-task question — a dialog, not a curses picker."""
        questions = args.get("questions") or []
        if not isinstance(questions, list) or not questions:
            return "Error: ask_user requires a non-empty `questions` list."
        token = self.pending.open()
        self.emit(Event.ASK_USER, token=token, questions=questions,
                  turn_id=turn_id)
        try:
            answers = self.pending.wait(token, timeout=ASK_TIMEOUT)
        except Cancelled:
            return ("The user dismissed the question without answering. Choose "
                    "the most reasonable option yourself, say which you chose "
                    "and why, and continue.")
        if not answers:
            return ("The user dismissed the question without answering. Choose "
                    "the most reasonable option yourself, say which you chose "
                    "and why, and continue.")
        lines = []
        for q, a in zip(questions, answers if isinstance(answers, list) else [answers]):
            label = q.get("question") if isinstance(q, dict) else str(q)
            lines.append(f"{label}\n  → {a}")
        return "The user answered:\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # background servers
    # ------------------------------------------------------------------
    def _start_server(self, args: dict, call_id: str) -> str:
        """Spawn a long-running HTTP server, wait for its port, return a URL.

        Ported from the CLI's hardened version — the simplified first cut
        re-introduced failure modes the CLI had already fixed live:
        - a port named IN the command ("http.server 8080") beats the auto
          pick, or we wait 45s on a port the server never binds;
        - a bare `python -m http.server` ignores PORT env — append the port;
        - restarting our OWN already-running server looped forever on
          "port busy";
        - stdout was only read on exit, so a server that logs >64KB before
          binding filled the pipe and DEADLOCKED — an output-drain thread
          keeps it flowing and gives the error paths a tail to show.
        """
        cmd = (args.get("command") or "").strip()
        if not cmd:
            return "Error: start_server requires a `command`."

        # Port precedence: explicit in the COMMAND > `port` arg > auto-pick.
        cmd_port = _extract_command_port(cmd)
        want = args.get("port")
        try:
            arg_port = int(want) if want is not None else None
        except (TypeError, ValueError):
            arg_port = None
        if cmd_port is not None:
            port, source = cmd_port, "command"
        elif arg_port is not None:
            port, source = arg_port, "arg"
        else:
            port, source = _free_port(), "auto"

        if source != "auto" and _port_open(port):
            for srv in self.servers:
                if srv.get("port") == port:
                    return (
                        f"Error: port {port} is YOUR OWN server started "
                        f"earlier this session — already running at "
                        f"{srv['url']} (pid {srv['pid']}). Do NOT start it "
                        "again; just tell the user the URL.")
            return (f"Error: port {port} is already in use. Change the port "
                    "or omit it to auto-pick a free one.")

        appended = False
        if cmd_port is None:
            cmd, appended = _maybe_append_port(cmd, port)

        try:
            wait_seconds = int(args.get("wait_seconds") or 30)
        except (TypeError, ValueError):
            wait_seconds = 30
        wait_seconds = max(5, min(300, wait_seconds))

        env = dict(os.environ, PORT=str(port), BROWSER="none")
        try:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=str(self.workspace), env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        except Exception as e:
            return f"Error: could not start the server ({type(e).__name__}: {e})"

        # Drain output so the pipe can never fill and block the server.
        out_lines: list = []
        out_lock = threading.Lock()

        def _drain() -> None:
            try:
                for line in iter(proc.stdout.readline, ""):
                    with out_lock:
                        out_lines.append(line.rstrip("\n"))
                        if len(out_lines) > 1000:
                            del out_lines[: len(out_lines) - 1000]
            except Exception:
                pass

        threading.Thread(target=_drain, daemon=True,
                         name=f"server-{proc.pid}-drain").start()

        def _tail() -> str:
            with out_lock:
                return "\n".join(out_lines[-30:]) or "(no output)"

        deadline = time.time() + wait_seconds
        next_scan = time.time() + 2.0
        while time.time() < deadline:
            rc = proc.poll()
            if rc is not None and rc != 0:
                return (f"Error: the server exited with code {rc} before "
                        f"opening port {port}.\nOutput:\n{_tail()}")
            if rc == 0:
                return ("Error: the command exited 0 without leaving a "
                        "listening server behind. If it daemonizes, keep it "
                        f"in the foreground instead.\nOutput:\n{_tail()}")
            # Adoption scan: what did the process group ACTUALLY bind? A
            # server that ignores PORT env (vite with a configured port,
            # anything with its own default) is adopted in ~2s instead of
            # timing out at 30 while it runs fine.
            if time.time() >= next_scan:
                next_scan = time.time() + 2.0
                for found in _discover_listen_ports(proc.pid):
                    if found != port and _port_open(found):
                        port = found
                        break
            if _port_open(port):
                url = f"http://localhost:{port}"
                rec = {"pid": proc.pid, "port": port, "cmd": cmd, "url": url,
                       "proc": proc}
                self.servers.append(rec)
                self.emit(Event.SERVER_STARTED, pid=proc.pid, port=port,
                          url=url, cmd=cmd, call_id=call_id)
                self._status()
                extra = ("\nNOTE: the port was appended to your command — "
                         "http.server ignores the PORT env var."
                         if appended else "")
                return (f"Server started: {url} (pid {proc.pid}). It runs in "
                        "the background and the user can already see it. Do "
                        "not curl it, do not re-read the files — end your "
                        "turn with a brief acknowledgment." + extra)
            time.sleep(0.25)

        _kill(proc.pid)
        return (f"Error: timed out after {wait_seconds}s — the process never "
                f"opened port {port}. Killed it. start_server is for HTTP "
                "servers; a GUI app or background worker has no port to wait "
                "on — launch those with run_bash instead. If the server takes "
                "a fixed port, put it in the command ('--port 3000', "
                "'localhost:8000', or 'http.server 8080').\n"
                f"Output so far:\n{_tail()}")

    def stop_server(self, pid: int) -> bool:
        for rec in list(self.servers):
            if rec.get("pid") == pid:
                _kill(pid)
                self.servers.remove(rec)
                self.emit(Event.SERVER_STOPPED, pid=pid, port=rec.get("port"))
                self._status()
                return True
        return False

    def shutdown_servers(self) -> None:
        for rec in list(self.servers):
            _kill(rec.get("pid"))
        self.servers.clear()


# ----------------------------------------------------------------------
# helpers — ported verbatim in behaviour from cli.py
# ----------------------------------------------------------------------
def _prepare_call(tc: dict) -> dict:
    """Classify one accumulated tool call: parse, normalize, repair.

    Lifted from cli.py unchanged in substance. `history_args` is the load-
    bearing field: the model must never be replayed its own malformed JSON,
    or it few-shot-primes itself into repeating the mistake.
    """
    from .core.tools import repair_tool_args, validate_call

    raw = tc.get("arguments") or ""
    p = {"id": tc["id"], "name": tc["name"], "raw": raw, "args": {},
         "history_args": "{}", "kind": "invalid", "error": "", "pos": None}
    stripped = raw.strip()
    if not stripped:
        p["error"] = validate_call(p["name"], {}) or (
            f"Error: {p['name']} received empty arguments.")
        return p
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as e:
        p["pos"], p["error"] = e.pos, str(e)
        try:
            lenient = json.loads(stripped, strict=False)
        except json.JSONDecodeError:
            lenient = None
        if isinstance(lenient, dict):
            p["args"] = lenient
            p["history_args"] = json.dumps(lenient, ensure_ascii=False)
            err = validate_call(p["name"], lenient)
            p["kind"], p["error"] = ("invalid", err) if err else ("normalized", "")
            return p
        repaired, reason = repair_tool_args(stripped)
        if repaired is not None:
            fixed = json.loads(repaired, strict=False)
            p["args"] = fixed
            p["history_args"] = json.dumps(fixed, ensure_ascii=False)
            err = validate_call(p["name"], fixed)
            p["kind"], p["error"] = ("invalid", err) if err else ("repaired", "")
            return p
        p["kind"] = "truncated" if reason == "truncated" else "unparseable"
        return p
    if not isinstance(obj, dict):
        p["error"] = (f"Error: {p['name']} arguments must be a single JSON "
                      f"object, got {type(obj).__name__}.")
        return p
    p["args"] = obj
    err = validate_call(p["name"], obj)
    if err:
        p["history_args"], p["error"] = raw, err
        return p
    p["kind"], p["history_args"] = "ok", raw
    return p


def _doom_feedback(p: dict, streak: int) -> str:
    from .core.tools import parse_error_context, schema_hint
    name = p["name"]
    if p["kind"] == "truncated":
        body = (f"Error: your {name} arguments were cut off mid-stream and "
                "could not be parsed. Do not repeat the same call — send it "
                "again, smaller.")
    elif p["kind"] == "unparseable":
        window = parse_error_context(p["raw"], p["pos"]) if p["pos"] is not None else ""
        body = (f"Error: your {name} arguments were not valid JSON "
                f"({p['error']}). {window}").strip()
    else:
        body = p["error"] or f"Error: invalid arguments for {name}."
    hint = schema_hint(name)
    if hint:
        body += f"\n{hint}"
    if streak >= 2:
        body += ("\nYou have now failed this call twice. Try a materially "
                 "different approach — a smaller payload, or a different "
                 "tool — or tell the user what is blocking you.")
    return body


def _safe_args(name: str, args: dict) -> dict:
    """Args for the UI. write_file content is clipped — the diff preview
    carries the body, and a megabyte of it has no business on the wire twice."""
    if name == "write_file":
        return {"path": args.get("path"), "bytes": len(args.get("content") or "")}
    return {k: (_clip(v, 4000) if isinstance(v, str) else v)
            for k, v in (args or {}).items()}


def mode_from_str_safe(v) -> Mode:
    try:
        from .core.permissions import from_str
        return from_str(str(v or "default"))
    except Exception:
        return Mode.DEFAULT


def _task_name(got: dict, rung: str) -> "str | None":
    """The task level as the user reads it — only for picks made from the
    per-level scores, where the five rungs carry these names."""
    if not got.get("scored"):
        return None
    return router.LEVEL_NAMES.get(router._band(rung))


def _local_bill(usage: "dict | None") -> dict:
    """A local step's split: its real token counts, every dollar part $0."""
    bill = _pricing.empty_bill()
    try:
        tokens = _pricing.usage_tokens(usage)
        for part in ("fresh", "cached", "cache_write", "output"):
            bill[f"{part}_tokens"] = tokens[part]
    except Exception:
        pass
    return bill


def _pick_label(got: dict) -> str:
    """A pick as the user reads it: the model, and the thinking level when the
    router chose one."""
    level = got.get("level")
    name = _short_model(got.get("model", ""))
    return f"{name} · {level}" if level and level != "default" else name


def _short_model(m: str) -> str:
    """Drop the provider prefix for a compact notice: openai/gpt-x -> gpt-x."""
    return m.split("/", 1)[1] if "/" in (m or "") else (m or "")


def _retire_old_images(messages: list) -> None:
    """Replace image parts of past turns with a one-line note.

    A pasted screenshot is ~600KB of base64 re-sent on every hop; it earns
    its place in the turn it was sent, not for the rest of the session.
    """
    for m in messages:
        if m.get("role") != "user" or not isinstance(m.get("content"), list):
            continue
        parts = m["content"]
        if not any(p.get("type") == "image_url" for p in parts):
            continue
        kept = [p for p in parts if p.get("type") != "image_url"]
        n = len(parts) - len(kept)
        kept.append({"type": "text", "text":
                     f"[{n} image{'s' if n != 1 else ''} attached here earlier; "
                     "no longer included. Ask the user to re-attach if needed.]"})
        m["content"] = kept


def _clip(s: str, n: int = 8000) -> str:
    s = s if isinstance(s, str) else str(s)
    return s if len(s) <= n else s[:n] + f"\n…[+{len(s) - n} chars]"


def _safe_text(resp) -> str:
    try:
        return resp.text[:2000]
    except Exception:
        return ""


def _free_port(start: int = _PORT_RANGE[0], end: int = _PORT_RANGE[1]) -> int:
    for port in range(start, end + 1):
        if not _port_open(port):
            return port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex((host, port)) == 0


_FLAG_PORT_RE = re.compile(r"(?:--?port[=\s]+|-p[=\s]+)(\d{2,5})")
_COLON_PORT_RE = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})")
# `python -m http.server` (optionally with flags) — it IGNORES the PORT env
# var and binds 8000 unless the port is a positional arg, so we must append it.
_HTTP_SERVER_RE = re.compile(
    r"^\s*\S*python[\d.]*(?:\s+-[a-zA-Z]+)*\s+-m\s+http\.server"
    r"(?:\s+--?\S+(?:\s+\S+)?)*\s*$")


def _extract_command_port(cmd: str) -> "int | None":
    """The explicit port named in the command itself, or None. Biased against
    false positives — a miss costs a couple seconds, a false positive waits on
    the wrong port. flag > colon > trailing bare token; last match wins."""
    for rx in (_FLAG_PORT_RE, _COLON_PORT_RE):
        hits = [int(m.group(1)) for m in rx.finditer(cmd)]
        hits = [h for h in hits if 1 <= h <= 65535]
        if hits:
            return hits[-1]
    bare = [int(t) for t in cmd.split() if t.isdigit() and 1024 <= int(t) <= 65535]
    return bare[-1] if bare else None


def _discover_listen_ports(pgid: int) -> list:
    """TCP ports the process GROUP is actually listening on, via lsof.

    This is what turns "vite ignored PORT and bound 4000, so we timed out
    waiting on 5173 while a working server ran" into a 2-second adoption
    instead of a 30s failure. Best-effort: no lsof, no ports.
    """
    try:
        out = subprocess.run(
            ["lsof", "-a", "-g", str(pgid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
            capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return []
    ports = []
    for line in out.splitlines():
        if line.startswith("n"):
            m = re.search(r":(\d+)$", line.strip())
            if m:
                try:
                    port = int(m.group(1))
                    if 1 <= port <= 65535 and port not in ports:
                        ports.append(port)
                except ValueError:
                    pass
    return ports


def _maybe_append_port(cmd: str, port: int) -> "tuple[str, bool]":
    """http.server ignores PORT env, so a bare invocation can never open the
    port we wait on. Append it for exactly that shape. Returns (cmd, appended)."""
    if _HTTP_SERVER_RE.match(cmd):
        return f"{cmd.rstrip()} {port}", True
    return cmd, False


def _kill(pid) -> None:
    if not pid:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
