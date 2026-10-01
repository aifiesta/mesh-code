"""The wire protocol between the engine and any frontend.

This module is the whole reason the harness is not a second agent loop.
The CLI's turn logic and its terminal drawing were interleaved — every
decision point also printed something, so the only way to get a second
frontend was to fork the loop. Here the loop makes decisions and emits
EVENTS; drawing them is somebody else's job. The desktop UI is the first
consumer, but nothing below is specific to it.

Two directions:

  Event    engine -> frontend.  Facts about what happened. Fire-and-forget.
  Request  frontend -> engine.  Commands, plus REPLIES to the two events
                                that block the agent loop (approval and
                                ask_user).

The blocking pair is the important part. A terminal agent asks `y/n` on
stdin and reads the answer inline; a windowed agent cannot. So approval
becomes a round trip: the engine emits TOOL_PROPOSED carrying a
correlation id, parks the turn, and resumes when a matching
APPROVAL_RESPONSE arrives. Same shape for ask_user. Every other event is
one-way and never blocks anything.

Events are JSON objects with a `type`, a monotonic `seq`, the `session`
they belong to, and a `data` payload. `seq` lets a reconnecting client
tell whether it missed anything.
"""
from __future__ import annotations

import itertools
import time
from typing import Any

PROTOCOL_VERSION = 1


class Event:
    """engine -> frontend."""

    # ---- lifecycle
    SESSION_STARTED = "session.started"
    HISTORY = "history"              # replay of a session's conversation for the UI
    CHAT_CLEARED = "chat.cleared"    # the session's conversation was reset
    JOURNAL = "journal"              # the project journal markdown
    SESSION_CLOSED = "session.closed"
    WORKSPACE_CHANGED = "workspace.changed"

    # ---- one user prompt and everything it causes
    TURN_STARTED = "turn.started"
    TURN_FINISHED = "turn.finished"
    TURN_ABORTED = "turn.aborted"
    HOP_STARTED = "hop.started"          # one model round trip within a turn

    # ---- assistant output
    ASSISTANT_DELTA = "assistant.delta"      # streamed token(s)
    ASSISTANT_MESSAGE = "assistant.message"  # the settled full text
    ASSISTANT_PROGRESS = "assistant.progress"  # tool args still streaming

    # ---- tools
    TOOL_PROPOSED = "tool.proposed"    # BLOCKS: awaits APPROVAL_RESPONSE
    TOOL_DECIDED = "tool.decided"      # how the proposal resolved
    TOOL_STARTED = "tool.started"
    TOOL_RESULT = "tool.result"
    TOOL_SKIPPED = "tool.skipped"      # malformed args, never executed
    TOOL_REPAIRED = "tool.repaired"    # args were fixed client-side

    # ---- rich side-channels the UI renders as panels
    PLAN_UPDATED = "plan.updated"
    SERVER_STARTED = "server.started"
    SERVER_STOPPED = "server.stopped"
    FILE_CHANGED = "file.changed"      # drives the diff view
    MEMORY_NOTE = "memory.note"
    ASK_USER = "ask.user"              # BLOCKS: awaits ASK_RESPONSE

    # ---- session-wide status
    STATUS = "status"                  # model / mode / route / cost
    NOTICE = "notice"                  # info | warn — non-fatal
    ERROR = "error"
    COMPACTED = "compacted"
    ROUTED = "routed"                  # smart router picked a model
    SETTINGS = "settings"              # schema + current values
    CATALOG = "catalog"                # model list with context + pricing
    ROUTE_EXPLAIN = "route.explain"    # ranked candidates for a draft prompt
    CONFIG_ERROR = "config.error"      # a rejected setting, with the reason
    DIR_LISTING = "dir.listing"        # children of one workspace directory
    FILE_PREVIEW = "file.preview"      # a workspace file, for the viewer
    FILE_INDEX = "file.index"          # every file path under a root, for @-mentions
    FILE_OP = "file.op"                # result of a create/rename/delete
    TERM_OUTPUT = "term.output"        # one line from the user's shell
    TERM_READY = "term.ready"
    FOLDERS = "folders"                # the open roots, and which is active


class Request:
    """frontend -> engine."""

    PROMPT = "prompt"
    INTERRUPT = "interrupt"
    APPROVAL_RESPONSE = "approval.response"   # reply to TOOL_PROPOSED
    ASK_RESPONSE = "ask.response"             # reply to ASK_USER
    SET_MODE = "set.mode"
    SET_MODEL = "set.model"
    SET_ROUTE = "set.route"
    SET_STYLE = "set.style"
    OPEN_WORKSPACE = "open.workspace"
    ADD_FOLDER = "add.folder"        # another browsable root, same window
    REMOVE_FOLDER = "remove.folder"
    SET_ACTIVE_FOLDER = "set.active.folder"  # which root the AGENT works in
    NEW_SESSION = "new.session"
    NEW_CHAT = "new.chat"            # reset THIS session's conversation (keeps servers)
    GET_HISTORY = "get.history"      # ask for a replayable conversation history
    GET_JOURNAL = "get.journal"      # ask for the project journal
    CLOSE_SESSION = "close.session"
    LIST_MODELS = "list.models"
    STOP_SERVER = "stop.server"
    COMPACT = "compact"
    SAVE_KEY = "save.key"
    CLEAR_KEY = "clear.key"       # forget the key this app stored
    SET_CONFIG = "set.config"        # one validated setting, from any surface
    ROUTE_PREVIEW = "route.preview"  # "which model would you pick, and why?"
    LIST_DIR = "list.dir"            # one directory of the workspace tree
    READ_FILE = "read.file"          # preview a workspace file in the viewer
    FIND_FILES = "find.files"        # flat list of a root's files, for the @ picker
    CREATE_ENTRY = "create.entry"    # new file or folder
    RENAME_ENTRY = "rename.entry"
    DELETE_ENTRY = "delete.entry"    # permanent; the UI confirms first
    TERM_RUN = "term.run"            # the USER's own shell, not the agent's
    TERM_INTERRUPT = "term.interrupt"
    TERM_CLOSE = "term.close"


# Approval verdicts. ALWAYS is "auto-approve this tool for the rest of the
# session" — the CLI's `a` answer, which the UI shows as a third button.
class Decision:
    ALLOW = "allow"
    DENY = "deny"
    ALWAYS = "always"
    # Internal only — never sent by the UI. Marks "the wait was cancelled by
    # a turn interrupt", so the recorded tool result is an interrupt stub
    # rather than "user denied", which would mislead the next turn's model.
    INTERRUPTED = "interrupted"


_seq = itertools.count(1)


def event(type_: str, session: str | None = None, **data: Any) -> dict:
    """Build one wire event. `seq` is process-global and monotonic."""
    return {
        "v": PROTOCOL_VERSION,
        "seq": next(_seq),
        "type": type_,
        "session": session,
        "ts": time.time(),
        "data": data,
    }
