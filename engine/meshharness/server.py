"""Local transport: one WebSocket, many sessions, one window each.

SECURITY — read before changing anything here.

This process can write files and run shell commands. Exposing that over a
local socket is exactly as dangerous as it sounds, so the socket is
defended three ways and all three matter:

  1. It binds 127.0.0.1 only. Never 0.0.0.0 — that would put a remote code
     execution endpoint on the user's LAN.
  2. Every connection must present a token minted at startup and handed to
     the UI out of band (argv/env from the Electron main process). Without
     it, any other program on the machine could drive the agent.
  3. The Origin header is checked. A browser tab on any website can open a
     ws:// connection to localhost — same-origin policy does not apply to
     WebSockets — so without this check a malicious page could try to talk
     to the engine. Non-browser clients send no Origin and are allowed;
     a browser-supplied Origin must be one we recognise.

The token is also why the engine picks an ephemeral port and reports it on
stdout rather than using a fixed one: nothing to guess, nothing to squat.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import base64
import mimetypes
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
from contextlib import asynccontextmanager

# The protocol's own `Request` enum (imported below) owns the bare name;
# FastAPI's request object comes in aliased so the two never collide.
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi import Request as HttpRequest
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import files as files_mod
from . import settings as settings_mod
from .core import __version__
from .core import config as config_mod
from .core.permissions import Mode, from_str as mode_from_str
from .protocol import Event, Request, event
from .session import Session

# Exact origins a BROWSER may present. A prefix match here was unsafe:
# "http://localhost.evil.com" .startswith("http://localhost") is True, so a
# hostile site would clear the anti-CSRF gate. Electron/file origins send no
# Origin at all (allowed below); a browser Origin must match one of these
# exactly, on any port for the loopback hosts.
_ALLOWED_ORIGIN_HOSTS = ("localhost", "127.0.0.1", "[::1]")
_ALLOWED_ORIGIN_SCHEMES = ("file", "app")  # Electron packaged / dev


def _origin_ok(origin: "str | None") -> bool:
    """True if this Origin may open the socket. No Origin = non-browser = ok."""
    if not origin:
        return True
    from urllib.parse import urlsplit
    try:
        u = urlsplit(origin)
    except ValueError:
        return False
    if u.scheme in _ALLOWED_ORIGIN_SCHEMES:
        return True
    if u.scheme in ("http", "https") and u.hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    return False


class Engine:
    """Owns every live session and the fan-out to connected windows."""

    def __init__(self, token: str):
        self.token = token
        self.sessions: dict[str, Session] = {}
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.outbox: asyncio.Queue = asyncio.Queue()
        # Strong refs to the background tasks. asyncio only holds WEAK
        # references to running tasks, so a bare create_task() whose
        # result nobody keeps can be garbage-collected the moment it
        # suspends — which is exactly what pump() does on its first
        # await. Dropping it silently stops all event delivery.
        self._tasks: set = set()
        # Session worker threads all funnel usage tallies through emit();
        # without a lock two turns finishing at once can lose a tally.
        self._usage_lock = threading.Lock()
        self.cfg = config_mod.load_config()
        self._catalog: list | None = None
        self.usage = config_mod.load_usage()
        # Browsable roots for the explorer, keyed by absolute path. The
        # ACTIVE one is the session's workspace — the only place the agent
        # reads, writes or runs commands. Extra folders are for looking at
        # code and copying paths, which is why adding one does not widen
        # any safety boundary.
        self.roots: list[str] = []
        # Restore persisted sessions so an app restart lands you back in
        # your conversations — the single biggest "it feels broken" before
        # this existed: every restart looked like total amnesia.
        self._restore_sessions()

    def _restore_sessions(self) -> None:
        try:
            sdir = config_mod.SESSIONS_DIR
            if not sdir.is_dir():
                return
            files = sorted(sdir.glob("*.json"),
                           key=lambda f: f.stat().st_mtime)[-20:]
            for f in files:
                try:
                    data = json.loads(f.read_text())
                    if not data.get("id") or not data.get("workspace"):
                        continue
                    if not Path(data["workspace"]).is_dir():
                        continue  # the project folder is gone
                    sess = Session.restore(data, dict(self.cfg), self.emit)
                    sess.catalog = self._catalog
                    self.sessions[sess.id] = sess
                    self.add_root(str(sess.workspace))
                except Exception:
                    continue
        except Exception:
            pass

    # -- emit bridges the worker threads back onto the event loop ---------
    def emit(self, ev: dict) -> None:
        # Central place to tally what the app has spent: every session's
        # turns pass through here on their way to the frontend.
        # Count both natural and aborted ends: a turn the user stopped after
        # real spend must still reach the lifetime tally, or the total drifts
        # down with every interrupt.
        if ev.get("type") in (Event.TURN_FINISHED, Event.TURN_ABORTED,
                              Event.SESSION_STARTED, Event.CHAT_CLEARED):
            sess = self.sessions.get(ev.get("session") or "")
            if sess:
                sess.save_state()
        if ev.get("type") in (Event.TURN_FINISHED, Event.TURN_ABORTED):
            d = ev.get("data") or {}
            with self._usage_lock:
                try:
                    self.usage["cost"] = round(
                        float(self.usage.get("cost", 0)) + float(d.get("cost") or 0), 8)
                    self.usage["tokens"] = int(self.usage.get("tokens", 0)) + int(d.get("tokens") or 0)
                    self.usage["turns"] = int(self.usage.get("turns", 0)) + 1
                    self.usage.setdefault("since", time.time())
                    config_mod.save_usage(self.usage)
                except (TypeError, ValueError):
                    pass
        loop = self.loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self.outbox.put_nowait, ev)
        except RuntimeError:
            pass  # loop is shutting down

    def spawn(self, coro) -> asyncio.Task:
        """create_task + keep a strong reference until it finishes."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def pump(self) -> None:
        """Fan every queued event out to every connected window."""
        while True:
            ev = await self.outbox.get()
            dead = []
            for ws in list(self.clients):
                try:
                    await ws.send_text(json.dumps(ev, default=str))
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.clients.discard(ws)

    # -- sessions ---------------------------------------------------------
    def open_session(self, workspace: str, *, mode: str = "default",
                     model: str | None = None, force_new: bool = False) -> Session:
        """Open a workspace, REUSING the session already on it if there is one.

        This used to mint a new session unconditionally, so promoting a
        browsed folder — or reopening a project from the sidebar — replaced a
        live conversation with an empty one. The old session kept running in
        the background with its transcript unreachable, which reads as "my
        chat disappeared".
        """
        target = str(Path(workspace).expanduser().resolve())
        if not force_new:
            # A project can hold several chats — plain "open this project"
            # lands in the one most recently used.
            matches = sorted(
                (x for x in self.sessions.values() if str(x.workspace) == target),
                key=lambda x: getattr(x, "last_active", 0), reverse=True)
            for existing in matches[:1]:
                if str(existing.workspace) == target:
                    self.add_root(target)
                    # Apply what the caller asked for on the reuse path too —
                    # opening a project "in bypass" or with a different model
                    # used to be silently ignored, landing you in the old
                    # session's settings with no signal.
                    if model and model != existing.cfg.get("model"):
                        existing.set_model(model)
                    if mode and mode != existing.mode.value:
                        existing.set_mode(mode_from_str(mode))
                    existing.start_again()
                    return existing

        sid = uuid.uuid4().hex[:12]
        cfg = dict(self.cfg)
        if model:
            cfg["model"] = model
        self.add_root(workspace)
        s = Session(sid, workspace, cfg, self.emit, mode=mode_from_str(mode))
        self.sessions[sid] = s
        s.catalog = self._catalog
        s.start()
        return s

    def close_session(self, sid: str) -> None:
        s = self.sessions.pop(sid, None)
        if s:
            s.close()
            try:
                (config_mod.SESSIONS_DIR / f"{sid}.json").unlink(missing_ok=True)
            except OSError:
                pass

    async def catalog(self, force: bool = False) -> list:
        """Model catalog, fetched and shared by every session (it feeds the
        picker, the router's feasibility filter, and context limits).

        A FAILED fetch used to cache [] for the life of the process, so an
        app launched offline (or before a key was entered) never got a
        catalog again — empty picker, no pricing, routing silently off,
        context stuck at the default. Now a non-empty result is cached
        permanently; an empty/failed one is retried on the next call after a
        short cooldown.
        """
        if force:
            # The gateway's list changes week to week (new models, new
            # prices); the permanent cache is right for a running session
            # and wrong for someone who just clicked "Refresh".
            self._catalog = None
            self._catalog_retry_after = 0.0
        if self._catalog:
            # A session opened before the first fetch (or restored on boot)
            # has no catalog and can't price a hop. Hand it over here too,
            # not only on the fetch path.
            for s in self.sessions.values():
                if not s.catalog:
                    s.catalog = self._catalog
            return self._catalog
        import time as _time
        now = _time.monotonic()
        if now < getattr(self, "_catalog_retry_after", 0.0):
            return self._catalog or []
        key = self.cfg.get("api_key")
        if not key:
            # No point calling /models with an empty bearer; try again once a
            # key exists (SAVE_KEY clears the cooldown).
            self._catalog_retry_after = now + 5
            return []
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    f"{self.cfg['base_url']}/models",
                    headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                data = r.json()
            fetched = data.get("data") if isinstance(data, dict) else data
            self._catalog = fetched or []
        except Exception:
            self._catalog = []
        if not self._catalog:
            self._catalog_retry_after = now + 15  # cool down, then retry
        for s in self.sessions.values():
            s.catalog = self._catalog
        return self._catalog

    def add_root(self, path: str) -> str:
        p = str(Path(path).expanduser().resolve())
        if p not in self.roots:
            self.roots.append(p)
        return p

    def root_for(self, session, root: str | None) -> Path:
        """Resolve a browse request to a registered root.

        Unregistered paths are refused outright: the explorer may only look
        inside folders the user explicitly opened, never anywhere a message
        happens to name.
        """
        if not root:
            return Path(session.workspace)
        p = str(Path(root).expanduser().resolve())
        if p not in self.roots:
            raise files_mod.OutsideWorkspace("that folder is not open")
        return Path(p)

    def folders_event(self, session) -> dict:
        return event(Event.FOLDERS, session.id if session else None,
                     roots=[{"path": r, "name": Path(r).name} for r in self.roots],
                     active=str(session.workspace) if session else None)

    def profile(self) -> dict:
        """Account-shaped facts for the profile panel.

        The key is reported as a HINT, never in full: the panel exists to
        answer "am I signed in, and with which key" — which the last four
        characters settle — and a window that prints a live credential is a
        credential one screenshot away from being leaked.
        """
        key = self.cfg.get("api_key") or ""
        return {
            "signed_in": bool(key),
            "key_hint": f"…{key[-4:]}" if len(key) >= 4 else "",
            "key_source": (
                "environment" if os.environ.get("MESHAPI_API_KEY") or
                os.environ.get("MESH_API_KEY")
                else "meshapi CLI" if not config_mod.CREDENTIALS_FILE.exists()
                and config_mod.CLI_CREDENTIALS_FILE.exists()
                else "this app"),
            "gateway": self.cfg.get("base_url", ""),
            "version": __version__,
            "config_dir": str(config_mod.CONFIG_DIR),
            "models": len(self._catalog or []),
            # This app's own tally. NOT an account balance — the gateway
            # exposes none to a data-plane key, and inventing one would be
            # worse than showing nothing.
            "lifetime": {
                "cost": round(float(self.usage.get("cost", 0)), 6),
                "tokens": int(self.usage.get("tokens", 0)),
                "turns": int(self.usage.get("turns", 0)),
                "since": self.usage.get("since"),
            },
            "account_url": "https://app.meshapi.ai",
        }

    def shutdown(self) -> None:
        for s in list(self.sessions.values()):
            s.close()
        self.sessions.clear()


def create_app(engine: Engine, ui_dir: Path | None = None) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine.loop = asyncio.get_running_loop()
        engine.spawn(engine.pump())
        engine.spawn(engine.catalog())
        try:
            yield
        finally:
            engine.shutdown()

    app = FastAPI(title="Mesh Code Engine", lifespan=lifespan)

    @app.get("/health")
    async def health():
        # Unauthenticated: liveness ONLY. It used to leak session count and
        # whether a key was configured to any local process; that is recon a
        # co-resident process should not get for free. Everything else on the
        # socket is token-gated.
        return {"ok": True}

    # Workspace files by URL, for the viewer's rendered modes: an <iframe>
    # for HTML and PDF, an <img> for images. The token rides in the PATH,
    # not the query, so a page's relative assets (./style.css) resolve to
    # a URL that still carries it. The root is base64url of its absolute
    # path and must be one the user opened — same gate as the tree.
    @app.get("/raw/{token}/{session}/{root_b64}/{path:path}")
    async def raw(token: str, session: str, root_b64: str, path: str):
        if not secrets.compare_digest(token, engine.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        s = engine.sessions.get(session)
        if s is None:
            return JSONResponse({"error": "no such session"}, status_code=404)
        try:
            pad = "=" * (-len(root_b64) % 4)
            root = base64.urlsafe_b64decode(root_b64 + pad).decode("utf-8")
            target = files_mod.resolve(engine.root_for(s, root), path)
        except (files_mod.OutsideWorkspace, ValueError, UnicodeDecodeError) as e:
            return JSONResponse({"error": str(e)}, status_code=403)
        if not target.is_file():
            return JSONResponse({"error": "not a file"}, status_code=404)
        media, _ = mimetypes.guess_type(str(target))
        return FileResponse(str(target), media_type=media or "application/octet-stream",
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/models")
    async def models(token: str = ""):
        if not secrets.compare_digest(token, engine.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return {"models": await engine.catalog()}

    # Where the sidebar's beta-feedback dialog lands: the "Mesh Code Beta
    # Feedback" Google Form. The renderer's CSP only allows connections to
    # this origin, so the engine relays — which also gets a real status
    # where a no-cors browser POST would be opaque. Entry ids come from the
    # form's FB_PUBLIC_LOAD_DATA_ and change only if a question is deleted
    # and recreated.
    feedback_form = ("https://docs.google.com/forms/d/e/"
                     "1FAIpQLSeQh7Dy-mq_ptD_zGocIztn_Owuxj-6UWujio57t9acTBdaPQ"
                     "/formResponse")
    feedback_entries = {"rating": "entry.1281435406",
                        "good": "entry.482666334",
                        "bad": "entry.489765974"}

    @app.post("/api/feedback")
    async def feedback(request: HttpRequest, token: str = ""):
        if not secrets.compare_digest(token, engine.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "bad json"}, status_code=400)
        rating = body.get("rating")
        if not isinstance(rating, int) or not 1 <= rating <= 5:
            return JSONResponse({"error": "rating must be 1-5"}, status_code=400)
        data = {feedback_entries["rating"]: str(rating)}
        for key in ("good", "bad"):
            text = str(body.get(key) or "").strip()[:5000]
            if text:
                data[feedback_entries[key]] = text
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(feedback_form, data=data)
        except httpx.HTTPError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
        if r.status_code != 200:
            return JSONResponse({"ok": False, "error": f"form returned {r.status_code}"},
                                status_code=502)
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        # --- gate 1: origin. A website must not be able to reach the agent.
        if not _origin_ok(ws.headers.get("origin")):
            await ws.close(code=4403)
            return
        # --- gate 2: token. Minted this launch, handed to the UI privately.
        token = ws.query_params.get("token", "")
        if not secrets.compare_digest(token, engine.token):
            await ws.close(code=4401)
            return

        await ws.accept()
        engine.clients.add(ws)
        await ws.send_text(json.dumps(event(
            "engine.ready",
            sessions=[s.describe() for s in engine.sessions.values()],
            has_key=bool(engine.cfg.get("api_key")),
            settings=settings_mod.describe(engine.cfg),
            catalog=engine._catalog or [],
            profile=engine.profile(),
        )))
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await handle_request(engine, msg)
        except WebSocketDisconnect:
            pass
        finally:
            engine.clients.discard(ws)

    if ui_dir and ui_dir.exists():
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    return app


async def handle_request(engine: Engine, msg: dict) -> None:
    """Route one frontend request. Never raises into the socket loop."""
    kind = msg.get("type")
    sid = msg.get("session")
    s = engine.sessions.get(sid) if sid else None

    try:
        if kind == Request.OPEN_WORKSPACE:
            path = msg.get("workspace") or str(Path.home())
            opened = engine.open_session(path, mode=msg.get("mode", "default"),
                                         model=msg.get("model"))
            engine.emit(engine.folders_event(opened))
            return

        if kind == Request.ADD_FOLDER:
            engine.add_root(msg.get("path") or "")
            engine.emit(engine.folders_event(s or next(iter(engine.sessions.values()), None)))
            return

        if kind == Request.REMOVE_FOLDER:
            p = str(Path(msg.get("path") or "").expanduser().resolve())
            # The active project stays: closing the folder the agent is
            # working in would leave the session pointing at nothing.
            active_of = next((x for x in engine.sessions.values()
                              if str(x.workspace) == p), None)
            if active_of is not None:
                engine.emit(event(Event.NOTICE, sid, level="warn",
                                  message="that folder is an open project"))
            elif p in engine.roots:
                engine.roots.remove(p)
            engine.emit(engine.folders_event(s))
            return

        if kind == Request.NEW_SESSION:
            # force_new: "New session" must ALWAYS create one, even when a
            # session for that workspace already exists — otherwise the
            # reuse path silently returns the existing chat.
            opened = engine.open_session(
                msg.get("workspace") or str(Path.home()), force_new=True)
            engine.emit(engine.folders_event(opened))
            return

        if kind == Request.SAVE_KEY:
            key = (msg.get("key") or "").strip()
            ok, detail = await _verify_key(engine.cfg["base_url"], key)
            if ok:
                config_mod.save_api_key(key)
                engine.cfg["api_key"] = key
                engine._catalog = None
                engine._catalog_retry_after = 0.0
                for sess in engine.sessions.values():
                    sess.cfg["api_key"] = key
                await engine.catalog()
            engine.emit(event("key.result", None, ok=ok, detail=detail,
                              profile=engine.profile()))
            return

        if kind == Request.CLEAR_KEY:
            # Only ever removes THIS app's credentials file. If the meshapi
            # CLI is signed in, load_config falls back to its key on the next
            # read — so we report what actually happens rather than claiming
            # a sign-out that did not occur.
            fell_back = ""
            try:
                config_mod.CREDENTIALS_FILE.unlink(missing_ok=True)
            except OSError as e:
                engine.emit(event("key.result", None, ok=False,
                                  detail=f"could not remove the stored key ({e})"))
                return
            cli_key = config_mod._load_cli_api_key()
            engine.cfg["api_key"] = cli_key
            for sess in engine.sessions.values():
                sess.cfg["api_key"] = cli_key
            if cli_key:
                fell_back = ("Removed this app's key. The meshapi CLI is still "
                             "signed in, so that key is now in use.")
            engine._catalog = None
            engine._catalog_retry_after = 0.0
            engine.emit(event("key.result", None, ok=bool(cli_key),
                              detail=fell_back or "Signed out.",
                              cleared=True, profile=engine.profile()))
            return

        if kind == Request.SET_CONFIG:
            # ONE validated write path for every setting, whichever surface
            # sent it — control bar, palette, or settings sheet. A bad value
            # is reported, never silently coerced or silently dropped.
            key, value = msg.get("key"), msg.get("value")
            try:
                clean = settings_mod.coerce(key, value)
            except settings_mod.InvalidSetting as e:
                engine.emit(event(Event.CONFIG_ERROR, sid, key=key, message=str(e)))
                return
            targets = [s] if s else list(engine.sessions.values())
            engine.cfg[key] = clean          # new sessions inherit it
            for sess in targets:
                sess.update_cfg(**{key: clean})
            try:
                # Persist only what the schema declares. engine.cfg also holds
                # runtime-resolved values (api_key, base_url) that are not
                # settings and must never be written by a settings save.
                declared = {k: engine.cfg[k] for k in settings_mod.BY_KEY
                            if k in engine.cfg}
                config_mod.save_config(declared)
            except OSError:
                pass                          # a failed persist must not block the change
            engine.emit(event(Event.SETTINGS, sid,
                              **settings_mod.describe(engine.cfg)))
            return

        if kind == Request.LIST_MODELS:
            engine.emit(event(Event.CATALOG, sid,
                              models=await engine.catalog(force=bool(msg.get("refresh"))),
                              refreshed=bool(msg.get("refresh"))))
            return

        if s is None:
            # A session-scoped request for an unknown sid (engine restarted,
            # session closed while the socket was down) used to vanish with
            # no signal — the UI's prompt just evaporated and the window sat
            # there looking hung. Tell the client so it can drop the ghost.
            if sid:
                engine.emit(event(Event.ERROR, sid, fatal=False,
                                  message="that session is no longer open",
                                  detail="It may have been closed, or the "
                                         "engine restarted. Open the project "
                                         "again to start a fresh session.",
                                  unknown_session=True))
            return

        if kind == Request.NEW_CHAT:
            s.new_chat()
            return

        if kind == Request.GET_HISTORY:
            engine.emit(event(Event.HISTORY, sid,
                              messages=s.history_payload()))
            return

        if kind == Request.GET_JOURNAL:
            from .core import journal as journal_mod
            engine.emit(event(Event.JOURNAL, sid,
                              text=journal_mod.load(s.workspace),
                              path=str(journal_mod.path_for(s.workspace))))
            return

        if kind == Request.PROMPT:
            s.submit(msg.get("text") or "", msg.get("attachments") or [],
                     refs=msg.get("refs") or [])
        elif kind == Request.INTERRUPT:
            s.interrupt()
        elif kind == Request.APPROVAL_RESPONSE:
            s.respond_approval(msg.get("token") or "", msg.get("decision") or "deny")
        elif kind == Request.ASK_RESPONSE:
            s.respond_ask(msg.get("token") or "", msg.get("answers"))
        elif kind == Request.SET_MODE:
            s.set_mode(mode_from_str(msg.get("mode") or "default"))
        elif kind == Request.SET_MODEL:
            s.set_model(msg.get("model") or s.cfg.get("model"))
        elif kind == Request.SET_ROUTE:
            s.update_cfg(route_mode=msg.get("route_mode", "off"))
        elif kind == Request.SET_STYLE:
            s.update_cfg(output_style=msg.get("style", "default"))
        elif kind == Request.LIST_DIR:
            try:
                root = engine.root_for(s, msg.get("root"))
                engine.emit(event(Event.DIR_LISTING, sid, root=str(root),
                                  **files_mod.list_dir(root, msg.get("path") or "")))
            except files_mod.OutsideWorkspace as e:
                engine.emit(event(Event.DIR_LISTING, sid, root=msg.get("root"),
                                  path=msg.get("path") or "", entries=[], error=str(e)))
        elif kind == Request.FIND_FILES:
            try:
                root = engine.root_for(s, msg.get("root"))
                engine.emit(event(Event.FILE_INDEX, sid, root=str(root),
                                  **files_mod.index(root)))
            except files_mod.OutsideWorkspace as e:
                engine.emit(event(Event.FILE_INDEX, sid, root=msg.get("root"),
                                  files=[], truncated=False, error=str(e)))
        elif kind == Request.READ_FILE:
            try:
                root = engine.root_for(s, msg.get("root"))
                preview = files_mod.read_preview(root, msg.get("path") or "")
                engine.emit(event(Event.FILE_PREVIEW, sid, root=str(root), **preview))
            except files_mod.OutsideWorkspace as e:
                engine.emit(event(Event.FILE_PREVIEW, sid, root=msg.get("root"),
                                  path=msg.get("path") or "", content="", error=str(e)))
        elif kind in (Request.CREATE_ENTRY, Request.RENAME_ENTRY, Request.DELETE_ENTRY):
            # One handler: all three share the same guards and the same
            # "re-list the parent so the tree updates" tail.
            path = msg.get("path") or ""
            try:
                root = engine.root_for(s, msg.get("root"))
                if kind == Request.CREATE_ENTRY:
                    res = files_mod.create(root, path, msg.get("kind") or "file")
                elif kind == Request.RENAME_ENTRY:
                    res = files_mod.rename(root, path, msg.get("name") or "")
                else:
                    res = files_mod.delete(root, path)
                engine.emit(event(Event.FILE_OP, sid, ok=True, op=kind,
                                  root=str(root), **res))
                parent = str(Path(res.get("path", path)).parent)
                parent = "" if parent in (".", "/") else parent
                engine.emit(event(Event.DIR_LISTING, sid, root=str(root),
                                  **files_mod.list_dir(root, parent)))
            except (files_mod.FileOpError, files_mod.OutsideWorkspace) as e:
                engine.emit(event(Event.FILE_OP, sid, ok=False, op=kind,
                                  root=msg.get("root"), path=path, error=str(e)))
        elif kind == Request.TERM_RUN:
            s.terminal().run(msg.get("command") or "")
        elif kind == Request.TERM_INTERRUPT:
            s.terminal().interrupt()
        elif kind == Request.TERM_CLOSE:
            if s._term is not None:
                s._term.close()
                s._term = None
        elif kind == Request.ROUTE_PREVIEW:
            engine.emit(event(Event.ROUTE_EXPLAIN, sid,
                              **s.explain_route(msg.get("text") or "")))
        elif kind == Request.COMPACT:
            s._compact_now("requested by the user")
        elif kind == Request.STOP_SERVER:
            s.stop_server(int(msg.get("pid") or 0))
        elif kind == Request.CLOSE_SESSION:
            engine.close_session(sid)
    except Exception as e:
        engine.emit(event(Event.ERROR, sid,
                          message=f"{type(e).__name__}: {e}", fatal=False))


async def _verify_key(base_url: str, key: str):
    if not key:
        return False, "empty key"
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{base_url}/models",
                            headers={"Authorization": f"Bearer {key}"})
    except Exception as e:
        return False, f"could not reach the gateway ({type(e).__name__})"
    if r.status_code == 401:
        return False, "the gateway rejected that key"
    if r.status_code >= 400:
        return False, f"gateway returned HTTP {r.status_code}"
    return True, "key verified"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="mesh-harness-engine")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free one")
    ap.add_argument("--token", default="", help="auth token (else generated)")
    ap.add_argument("--ui", default="", help="directory of built UI assets")
    args = ap.parse_args(argv)

    import uvicorn

    token = args.token or os.environ.get("MESH_HARNESS_TOKEN") or secrets.token_urlsafe(32)
    engine = Engine(token)
    ui_dir = Path(args.ui) if args.ui else None
    app = create_app(engine, ui_dir)

    cfg = uvicorn.Config(app, host="127.0.0.1", port=args.port,
                         log_level="warning", access_log=False)
    server = uvicorn.Server(cfg)

    async def run():
        await server.serve()

    # The parent (Electron) reads this line to learn where to connect. It is
    # the only thing on stdout, and it carries the token — which is why the
    # engine's stdout is a private pipe, never a terminal a user might share.
    async def announce():
        while not server.started:
            await asyncio.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        print(json.dumps({"ready": True, "port": port, "token": token}),
              flush=True)

    async def both():
        await asyncio.gather(run(), announce())

    try:
        asyncio.run(both())
    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()


if __name__ == "__main__":
    main()
