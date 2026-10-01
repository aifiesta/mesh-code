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
from .core import compact, loopguard, memory, router
from .core import journal, safety
from .core import tools as tools_mod
from .core.client import complete_chat, stream_chat
from .core.permissions import AUTO_APPROVE, Mode
from .core.plan import Plan
from .core.tools import TOOLS, build_system_prompt, find_stub_markers, summarize_call
from .terminal import Terminal
from .protocol import Decision, Event, event

PLAN_TOOLS = {"create_plan", "update_step"}
INTERACTIVE_TOOLS = {"ask_user"}
UNGATED_TOOLS = PLAN_TOOLS | INTERACTIVE_TOOLS | {"remember"}

# How long a proposed tool call waits for a click before it is treated as a
# denial. Generous — a user may well walk away mid-turn — but not infinite,
# so a closed window can never strand a worker thread forever.
APPROVAL_TIMEOUT = 15 * 60
ASK_TIMEOUT = 30 * 60

_PORT_RANGE = (5173, 5199)


class Session:
    """One conversation against one workspace directory."""

    def __init__(self, session_id: str, workspace: str | Path, cfg: dict,
                 emit, *, title: str = "", mode: Mode = Mode.DEFAULT):
        self.id = session_id
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
        self.turn_cost = 0.0
        self.turn_tokens = 0
        self.turn_estimated = False
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
            optimize=self.cfg.get("optimize", 0.0),
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
            turn_cost=round(self.turn_cost, 6),
            turn_tokens=self.turn_tokens,
            turn_estimated=self.turn_estimated,
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
    def _system_prompt(self) -> str:
        prompt = build_system_prompt(self.cfg, cwd=self.workspace)
        # The project journal rides in the system prompt so even a fresh
        # session (or a small model) opens knowing how this project runs
        # and what is already built — instead of rediscovering it by
        # re-doing it. Gated by the same privacy toggle as repo memory.
        if self.cfg.get("repo_memory", True):
            block = journal.prompt_block(self.workspace)
            if block:
                prompt += "\n\n" + block
        return prompt

    def start(self) -> None:
        self.state["messages"] = [{
            "role": "system",
            "content": self._system_prompt(),
        }]
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
        self.state["messages"] = [{"role": "system", "content": self._system_prompt()}]
        self.chat_label = ""
        self.state["plan"] = None
        self.state["session_reads"] = {}
        self.state["stub_files"] = {}
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
                "messages": self.state.get("messages") or [],
                "plan": self.state["plan"].to_dict() if self.state.get("plan") else None,
            })
            fd = _os.open(str(path), _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
            with _os.fdopen(fd, "w") as f:
                f.write(payload)
        except Exception:
            pass  # persistence must never break a turn

    @classmethod
    def restore(cls, data: dict, cfg: dict, emit) -> "Session":
        """Rebuild a session from its snapshot — conversation intact."""
        s = cls(data["id"], data["workspace"], cfg, emit,
                title=data.get("title") or "",
                mode=mode_from_str_safe(data.get("mode")))
        s.created_at = data.get("created_at") or s.created_at
        s.chat_label = data.get("label") or ""
        s.session_cost = float(data.get("session_cost") or 0)
        s.session_tokens = int(data.get("session_tokens") or 0)
        if data.get("model"):
            s.cfg["model"] = data["model"]
        msgs = data.get("messages") or []
        if msgs:
            s.state["messages"] = msgs
        else:
            s.state["messages"] = [{"role": "system", "content": s._system_prompt()}]
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
        self.state.pop("_verify_fired", None)

        # Held on the session (not as locals) so _status() can publish the
        # running totals mid-turn. turn_model starts EMPTY on purpose: under
        # gateway routing the upstream picks, and we do not learn what ran
        # until the first hop answers. Claiming the pinned model before then
        # would be a guess presented as fact.
        self.turn_cost = 0.0
        self.turn_estimated = False
        self.turn_tokens = 0
        self.turn_model = None

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
                # A model that rejects reasoning_effort: record it and retry
                # without the field, keeping the setting for models that take
                # it. (Same evidence-based rule as the CLI — never trust the
                # catalog's supports_thinking flag.)
                if ("reasoning_effort" in msg
                        and self.cfg.get("reasoning_effort")
                        and self._effective_model() not in
                        (self.state.get("_reasoning_rejected") or set())):
                    self.state.setdefault("_reasoning_rejected", set()).add(
                        self._effective_model())
                    self.emit(Event.NOTICE, level="info", message=(
                        "this model rejected the reasoning-effort setting — "
                        "retrying without it"))
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
            # Billed figure by request id first (settles ~250ms after the
            # stream), then an in-body cost, then the catalog estimate.
            hop_cost, estimated = 0.0, False
            rids = meta.get("request_ids") or (
                [meta["request_id"]] if meta.get("request_id") else [])
            if rids and usage:
                try:
                    from .core import pricing
                    billed = pricing.fetch_request_cost(self.cfg, rids)
                except Exception:
                    billed = None
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
                            break
                    if guess:
                        hop_cost, estimated = float(guess), True
                except Exception:
                    pass
            self.turn_cost += hop_cost
            if estimated:
                self.turn_estimated = True
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
                if (weak and self.state.get("_smart_pick")
                        and not self.state.get("_verify_fired")):
                    self.state["_verify_fired"] = True
                    if self._smart_escalate(f"answer rejected ({weak})"):
                        continue  # retry the hop on the stronger model
                if has_reply:
                    self.emit(Event.ASSISTANT_MESSAGE, turn_id=turn_id, hop=hop,
                              text=reply, model=model_used)
                    self.state["messages"].append(
                        {"role": "assistant", "content": reply})
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
                      session_cost=round(self.session_cost, 6))
        else:
            self.emit(Event.TURN_FINISHED, turn_id=turn_id, hops=hop,
                      model=model_used, cost=round(turn_cost, 6),
                      cost_estimated=turn_estimated,
                      tokens=turn_tokens, elapsed=round(elapsed, 2),
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
            try:
                reply, meta = self._consume(stream_chat(msgs, cfg, tools=TOOLS))
            except KeyboardInterrupt:
                raise
            except httpx.HTTPStatusError as e:
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
                    got = self._non_streaming(msgs)
                    if got is not None:
                        return got
                raise
            except httpx.RequestError as e:
                if attempt < loopguard.MAX_STREAM_ATTEMPTS:
                    self._wait_retry(attempt, f"network error ({type(e).__name__})")
                    continue
                if not tried_non_streaming:
                    tried_non_streaming = True
                    got = self._non_streaming(msgs)
                    if got is not None:
                        return got
                raise

            err = meta.get("error")
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
                    # The optimized attempt aborted in-band and the raw retry
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

    def _non_streaming(self, msgs):
        """Last-resort blocking request — Mesh's own retry and provider
        fallback only cover non-streaming calls, so this is where a run of
        streaming failures can still recover."""
        try:
            self.emit(Event.NOTICE, level="info",
                      message="streaming keeps failing — trying one non-streaming request")
            return complete_chat(msgs, self.cfg, tools=TOOLS,
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
        rejected = self.state.get("_reasoning_rejected") or set()
        if cfg.get("reasoning_effort") and cfg["model"] in rejected:
            cfg.pop("reasoning_effort", None)
        return cfg

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
        self.turn_model = info["model"]

    def _route_turn(self, text: str, had_image: bool = False) -> None:
        """Initial per-turn pick. Fail-open: any miss leaves _smart_pick unset
        and the pinned model rides."""
        self.state["_smart_pick"] = None
        if self.cfg.get("route_mode") != "smart":
            return
        try:
            table = router.load_table()
            if not table or not self.catalog:
                return
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
            forced = self.cfg.get("route_effort", "auto")
            if forced != "auto":
                difficulty = forced
            elif inherit:
                difficulty = self.state.get("_smart_difficulty") or "mid"
            else:
                difficulty = router.estimate_difficulty(text)
            if inherit:
                cohort = self.state["_smart_cohort"]
            self.state["_smart_cohort"] = cohort
            self.state["_smart_base_difficulty"] = difficulty   # de-escalation floor
            weights = router.effective_weights(self.cfg.get("route_weights"), difficulty)
            exclude = set(self.cfg.get("exclude_models") or []) | set(self.state.get("_smart_bad") or set())
            got = router.pick(cohort, weights, table, self.catalog,
                              needs_tools=True, needs_ctx=self._needs_ctx(),
                              incumbent=self.state.get("_smart_last"),
                              exclude=exclude)
            if got and got.get("model"):
                self._apply_pick(got, difficulty)
                self.emit(Event.ROUTED, model=got["model"], cohort=cohort,
                          confidence=conf, difficulty=difficulty,
                          ranked=(got.get("ranked") or [])[:5])
        except Exception:
            self.state["_smart_pick"] = None  # never let routing break a turn

    def _smart_escalate(self, reason: str) -> bool:
        """The current model is visibly struggling — climb the effort ladder
        for the rest of this turn and re-pick. Bounded to _MAX_ESCALATIONS so
        a hard task reaches a frontier model but a broken one can't spend
        forever."""
        if self.cfg.get("route_mode") != "smart":
            return False
        if self.state.get("_smart_escalations", 0) >= self._MAX_ESCALATIONS:
            return False
        if self.cfg.get("route_effort", "auto") != "auto":
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
            weights = router.effective_weights(self.cfg.get("route_weights"), nxt)
            got = router.pick(cohort, weights, table, self.catalog,
                              needs_tools=True, needs_ctx=self._needs_ctx(),
                              incumbent=None,  # escalating: no stickiness
                              exclude=set(self.state.get("_smart_bad") or set()))
            if not got or not got.get("model"):
                return False
            prev = self.state.get("_smart_pick")
            self.state["_smart_escalations"] = self.state.get("_smart_escalations", 0) + 1
            self.state["_smart_clean_hops"] = 0
            got["escalated"] = True
            self._apply_pick(got, nxt)
            if got["model"] != prev:
                self.emit(Event.ROUTED, model=got["model"], cohort=cohort,
                          difficulty=nxt, escalated=True, reason=reason,
                          ranked=(got.get("ranked") or [])[:5])
                self.emit(Event.NOTICE, level="info",
                          message=f"escalating ({reason}) → {_short_model(got['model'])}")
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
            weights = router.effective_weights(self.cfg.get("route_weights"), nxt)
            got = router.pick(cohort, weights, table, self.catalog,
                              needs_tools=True, needs_ctx=self._needs_ctx(),
                              incumbent=self.state.get("_smart_last"),
                              exclude=set(self.state.get("_smart_bad") or set()),
                              incumbent_bonus=self._switch_cost_bonus())
            if not got or not got.get("model"):
                return
            prev = self.state.get("_smart_pick")
            self.state["_smart_escalations"] = max(0, self.state.get("_smart_escalations", 1) - 1)
            self.state["_smart_clean_hops"] = 0
            self._apply_pick(got, nxt)
            if got["model"] != prev:
                self.emit(Event.ROUTED, model=got["model"], cohort=cohort,
                          difficulty=nxt, settled=True,
                          ranked=(got.get("ranked") or [])[:5])
                self.emit(Event.NOTICE, level="info",
                          message=f"settling → {_short_model(got['model'])}")
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
            got = router.pick(cohort, weights, table, self.catalog,
                              needs_tools=True, incumbent=self.cfg.get("model"),
                              exclude=set(self.cfg.get("exclude_models") or []))
            out.update({
                "available": bool(got),
                "pick": (got or {}).get("model"),
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

        doomed = 0
        for p in prepared:
            if self._interrupt.is_set():
                return {"total": len(prepared), "doomed": doomed, "interrupted": True}

            name, args = p["name"], p["args"]

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

            try:
                result = self._execute_one(p, turn_id, hop)
            except KeyboardInterrupt:
                self._append_tool_result(p["id"], "Interrupted by the user.")
                return {"total": len(prepared), "doomed": doomed, "interrupted": True}
            except Exception as e:
                result = f"Error: tool execution raised {type(e).__name__}: {e}"
                self.emit(Event.TOOL_RESULT, call_id=p["id"], name=name,
                          ok=False, result=result)

            self._append_tool_result(p["id"], result)

        return {"total": len(prepared), "doomed": doomed, "interrupted": False}

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

        # --- gated: mode set, then the safety guards, then the user
        mode = self.mode          # live read, so a mid-batch change applies
        approved = name in AUTO_APPROVE.get(mode, set())
        safety_mode = mode
        if not approved and name in self.state.get("session_allow", set()):
            approved, safety_mode = True, Mode.AUTO

        reason = ""
        if approved:
            ok, why = self._safety_check(name, args, safety_mode)
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
        if name == "read_file":
            return self._read_file(args, p["id"])

        result = tools_mod.execute(name, args, self.cfg, cwd=self.workspace,
                                   cancel=self._interrupt)
        self.emit(Event.TOOL_RESULT, call_id=p["id"], name=name,
                  ok=not result.startswith("Error:"), result=_clip(result))
        if self._interrupt.is_set() and result.startswith("Error: interrupted"):
            raise KeyboardInterrupt
        return result

    def _safety_check(self, name: str, args: dict, mode: Mode):
        if name == "write_file":
            return safety.is_path_safe_for_auto_write(
                args.get("path"), mode, self.workspace)
        if name == "read_file":
            return safety.is_path_safe_for_auto_read(
                args.get("path"), mode, self.workspace)
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
        if name in ("run_bash", "start_server"):
            return {"kind": "command", "command": args.get("command") or "",
                    "cwd": str(self.workspace)}
        if name == "read_file":
            return {"kind": "path", "path": args.get("path") or ""}
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
            self.emit(Event.FILE_CHANGED, path=key, bytes=len(content),
                      action="write")
        return result

    def _read_file(self, args: dict, call_id: str) -> str:
        path = args.get("path") or ""
        # Same reason as _write_file: hand memory the workspace-resolved path.
        abspath = Path(path).expanduser()
        if not abspath.is_absolute():
            abspath = self.workspace / abspath
        abspath = str(abspath)
        stub = None
        try:
            stub = memory.dedupe_read(self.state, abspath,
                                      float(self.cfg.get("optimize") or 0))
        except Exception:
            stub = None
        if stub is not None:
            self.emit(Event.TOOL_RESULT, call_id=call_id, name="read_file",
                      ok=True, result=stub, deduped=True)
            return stub
        result = tools_mod.execute("read_file", args, self.cfg, cwd=self.workspace)
        ok = not result.startswith("Error:")
        self.emit(Event.TOOL_RESULT, call_id=call_id, name="read_file",
                  ok=ok, result=_clip(result), bytes=len(result))
        if ok:
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
            plan = Plan(steps)
            if not plan.steps:
                return "Error: all steps were empty after trimming whitespace."
            self.state["plan"] = plan
            self.emit(Event.PLAN_UPDATED, **plan.to_dict())
            return (f"Plan created with {len(plan.steps)} step(s). Now call "
                    "update_step(1, 'in_progress') and start work.")

        if name == "update_step":
            plan = self.state.get("plan")
            if plan is None:
                return "Error: no active plan. Call create_plan first."
            err = plan.update(args.get("index"), args.get("status"))
            if err:
                return f"Error: {err}"
            self.emit(Event.PLAN_UPDATED, **plan.to_dict())
            return f"Step {args['index']} → {args['status']}. {plan.summary()}"

        return f"Error: unknown plan tool `{name}`"

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
                        "not curl it, do not re-read the files — mark the "
                        "plan step completed and end your turn with a brief "
                        "acknowledgment." + extra)
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
