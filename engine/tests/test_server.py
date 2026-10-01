"""Transport tests: the socket's gates, and a full turn over the wire."""
import json
import os

import pytest
from fake_gateway import sse_turn
from fastapi.testclient import TestClient

from meshharness.protocol import Decision, Event, Request
from meshharness.server import Engine, create_app


@pytest.fixture
def app_client(gateway, monkeypatch, tmp_path):
    # load_config() reads the environment, so point the engine at the fake.
    monkeypatch.setenv("MESHAPI_BASE_URL", gateway.base_url)
    monkeypatch.setenv("MESHAPI_API_KEY", "rsk_test")
    engine = Engine(token="secret-token")
    engine.cfg["auto_compact"] = False
    engine.cfg["repo_memory"] = False
    engine.cfg["model"] = "test/model"
    with TestClient(create_app(engine)) as client:
        yield client, engine


def _drain(ws, want, limit=400):
    """Read until `want` shows up; return it.

    Fails fast on an engine error rather than blocking until the suite
    times out — a hang tells you nothing about what actually broke.
    """
    seen = []
    for _ in range(limit):
        ev = json.loads(ws.receive_text())
        seen.append(ev.get("type"))
        if ev.get("type") == want:
            return ev
        if ev.get("type") == Event.ERROR:
            raise AssertionError(
                f"engine errored while waiting for {want}: "
                f"{ev['data'].get('message')}")
    raise AssertionError(f"never saw {want}; saw {seen}")


def test_rejects_a_connection_with_no_token(app_client):
    client, _ = app_client
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass


def test_rejects_a_wrong_token(app_client):
    client, _ = app_client
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=guess"):
            pass


def test_rejects_a_browser_origin_we_do_not_know(app_client):
    """A page on the open web must not be able to drive the agent."""
    client, _ = app_client
    with pytest.raises(Exception):
        with client.websocket_connect(
                "/ws?token=secret-token",
                headers={"origin": "https://evil.example.com"}):
            pass


def test_accepts_the_right_token_and_announces_itself(app_client):
    client, _ = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "engine.ready"
        assert hello["data"]["has_key"] is True


def test_full_turn_over_the_socket(app_client, gateway, tmp_path):
    """Open a workspace, prompt, approve a write, see the file appear."""
    client, engine = app_client
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "write_file", "arguments":
                              json.dumps({"path": "wire.txt", "content": "over the wire"})}]),
        sse_turn("Done."))

    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())  # engine.ready
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        started = _drain(ws, Event.SESSION_STARTED)
        sid = started["session"]

        ws.send_text(json.dumps({"type": Request.PROMPT, "session": sid,
                                 "text": "write wire.txt"}))
        proposal = _drain(ws, Event.TOOL_PROPOSED)
        assert proposal["data"]["name"] == "write_file"

        ws.send_text(json.dumps({"type": Request.APPROVAL_RESPONSE,
                                 "session": sid,
                                 "token": proposal["data"]["token"],
                                 "decision": Decision.ALLOW}))
        _drain(ws, Event.TURN_FINISHED)

    assert (tmp_path / "wire.txt").read_text() == "over the wire"


def test_mode_change_arrives_and_is_reflected(app_client, tmp_path):
    client, engine = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        ws.send_text(json.dumps({"type": Request.SET_MODE, "session": sid,
                                 "mode": "bypass"}))
        for _ in range(50):
            ev = json.loads(ws.receive_text())
            if ev["type"] == Event.STATUS and ev["data"]["mode"] == "bypass":
                break
        else:
            raise AssertionError("mode change never reflected in status")
    assert engine.sessions[sid].mode.value == "bypass"


def test_health_endpoint(app_client):
    client, _ = app_client
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_models_endpoint_requires_the_token(app_client):
    client, _ = app_client
    assert client.get("/api/models").status_code == 401
    assert client.get("/api/models?token=secret-token").status_code == 200


# --- settings surface -------------------------------------------------------

def test_a_valid_setting_applies_and_echoes_the_new_schema(app_client, tmp_path):
    client, engine = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        hello = json.loads(ws.receive_text())
        # the schema ships with the greeting, so a UI can render controls
        # for settings it was never coded against
        keys = [s["key"] for s in hello["data"]["settings"]["settings"]]
        assert "route_mode" in keys and "optimize" in keys

        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "session": sid,
                                 "key": "route_mode", "value": "smart"}))
        _drain(ws, Event.SETTINGS)
    assert engine.sessions[sid].cfg["route_mode"] == "smart"
    assert engine.cfg["route_mode"] == "smart", "new sessions should inherit it"


def test_an_invalid_setting_is_refused_with_a_reason(app_client, tmp_path):
    client, engine = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "session": sid,
                                 "key": "optimize", "value": 9000}))
        err = _drain(ws, Event.CONFIG_ERROR)
    assert "between" in err["data"]["message"]
    assert engine.sessions[sid].cfg["optimize"] == 0.0, "must not be applied"


def test_route_preview_explains_a_pick_before_spending_anything(app_client, tmp_path):
    client, engine = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        ws.send_text(json.dumps({"type": Request.ROUTE_PREVIEW, "session": sid,
                                 "text": "refactor this class and fix the failing test"}))
        got = _drain(ws, Event.ROUTE_EXPLAIN)

    # The whole point is answering "which model, and why" BEFORE the turn
    # runs: cohort and difficulty come from the prompt alone (local, free),
    # and with a catalog loaded the router names an actual pick.
    d = got["data"]
    assert d["cohort"], "should classify the prompt"
    assert d["difficulty"] in ("low", "mid", "high")
    assert d["available"] is True
    assert d["pick"], "an available route must name the model it would use"
    assert set(d["weights"]) == {"cost", "cap", "speed"}
    # And when it CANNOT pick, it must say why rather than reporting a bare
    # "unavailable" the popover would render as an empty explanation.
    assert d.get("reason") is None or isinstance(d["reason"], str)


def test_saving_a_setting_never_persists_runtime_values(app_client, tmp_path):
    """Regression: a settings save must not write api_key or base_url.

    engine.cfg carries values resolved from the environment. Writing them
    back turned a test gateway's ephemeral port into a permanent base_url,
    which then broke the next real launch with connection-refused.
    """
    from meshharness.core import config as cfg_mod

    client, engine = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "session": sid,
                                 "key": "output_style", "value": "concise"}))
        _drain(ws, Event.SETTINGS)

    saved = json.loads(cfg_mod.CONFIG_FILE.read_text())
    assert saved["output_style"] == "concise", "the setting itself must persist"
    assert "api_key" not in saved
    assert "127.0.0.1" not in str(saved.get("base_url", "")), \
        "an env-resolved test base_url must never be written to disk"


def test_saving_a_key_reports_success_so_the_dialog_can_close(app_client, gateway):
    """Regression: key.result must carry an unambiguous outcome.

    The UI had no way to tell "verified and saved" from "still waiting", so
    the change-key dialog sat on "Verifying…" forever after a SUCCESS — the
    key was on disk the whole time. The event needs ok=True plus a refreshed
    profile so the panel updates in the same beat.
    """
    client, engine = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.SAVE_KEY, "key": "rsk_brand_new"}))
        for _ in range(60):
            ev = json.loads(ws.receive_text())
            if ev["type"] == "key.result":
                break
        else:
            raise AssertionError("no key.result was ever emitted")

    assert ev["data"]["ok"] is True, ev["data"].get("detail")
    assert ev["data"]["profile"]["signed_in"] is True
    assert ev["data"]["profile"]["key_hint"].endswith("_new")
    assert engine.cfg["api_key"] == "rsk_brand_new"


def test_a_rejected_key_is_reported_not_saved(app_client, monkeypatch):
    client, engine = app_client
    before = engine.cfg["api_key"]

    async def reject(base_url, key):
        return False, "the gateway rejected that key"
    monkeypatch.setattr("meshharness.server._verify_key", reject)

    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.SAVE_KEY, "key": "rsk_bad"}))
        for _ in range(60):
            ev = json.loads(ws.receive_text())
            if ev["type"] == "key.result":
                break
    assert ev["data"]["ok"] is False
    assert "rejected" in ev["data"]["detail"]
    assert engine.cfg["api_key"] == before, "a bad key must not be applied"


# --- workspace explorer -----------------------------------------------------

def test_explorer_lists_a_workspace_directory(app_client, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "README.md").write_text("# hi\n")

    client, _ = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        ws.send_text(json.dumps({"type": Request.LIST_DIR, "session": sid, "path": ""}))
        got = _drain(ws, Event.DIR_LISTING)["data"]

    names = [e["name"] for e in got["entries"]]
    # Directories first, noisy ones last among them, then files A-Z.
    assert names == ["src", "node_modules", "README.md"]
    assert next(e for e in got["entries"] if e["name"] == "node_modules")["noisy"]


def test_explorer_refuses_to_escape_the_workspace(app_client, tmp_path):
    """The tree reads the disk with no agent and no approval dialog in the
    loop, so the workspace boundary is the ONLY thing protecting it."""
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("do not read me")

    client, _ = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        for bad in ["../secret.txt", "/etc/passwd", "../../.."]:
            ws.send_text(json.dumps({"type": Request.READ_FILE,
                                     "session": sid, "path": bad}))
            got = _drain(ws, Event.FILE_PREVIEW)["data"]
            assert got.get("error"), f"{bad} was not refused"
            assert got["content"] == ""


def test_explorer_previews_text_and_flags_binary(app_client, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02binary")

    client, _ = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        ws.send_text(json.dumps({"type": Request.READ_FILE, "session": sid, "path": "a.py"}))
        text = _drain(ws, Event.FILE_PREVIEW)["data"]
        ws.send_text(json.dumps({"type": Request.READ_FILE, "session": sid, "path": "b.bin"}))
        binary = _drain(ws, Event.FILE_PREVIEW)["data"]

    assert text["content"] == "x = 1\ny = 2\n" and text["lines"] == 3
    assert binary["binary"] is True and binary["content"] == ""


def test_explorer_can_create_rename_and_delete(app_client, tmp_path):
    client, _ = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]

        def op(req, **kw):
            ws.send_text(json.dumps({"type": req, "session": sid, **kw}))
            return _drain(ws, Event.FILE_OP)["data"]

        assert op(Request.CREATE_ENTRY, path="notes", kind="dir")["ok"]
        assert (tmp_path / "notes").is_dir()
        assert op(Request.CREATE_ENTRY, path="notes/a.md", kind="file")["ok"]
        assert (tmp_path / "notes" / "a.md").is_file()
        assert op(Request.RENAME_ENTRY, path="notes/a.md", name="b.md")["ok"]
        assert (tmp_path / "notes" / "b.md").is_file()
        assert op(Request.DELETE_ENTRY, path="notes")["ok"]
        assert not (tmp_path / "notes").exists()


def test_file_ops_refuse_clobber_escape_and_root(app_client, tmp_path):
    """Writing raises the stakes: a create that overwrites, a rename that
    escapes, or a delete of the project root are all unrecoverable."""
    (tmp_path / "keep.txt").write_text("important")
    # A UNIQUE name: tmp_path.parent is pytest's shared basetemp, and another
    # test asserts that a plain `outside.txt` there does not exist. Reusing
    # the name makes the two tests fail depending on execution order.
    outside = tmp_path.parent / "outside-fileops.txt"
    outside.write_text("do not touch")

    client, _ = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]

        def op(req, **kw):
            ws.send_text(json.dumps({"type": req, "session": sid, **kw}))
            return _drain(ws, Event.FILE_OP)["data"]

        assert not op(Request.CREATE_ENTRY, path="keep.txt", kind="file")["ok"]
        assert not op(Request.CREATE_ENTRY, path="../escaped", kind="dir")["ok"]
        assert not op(Request.DELETE_ENTRY, path="")["ok"]
        assert not op(Request.DELETE_ENTRY, path="../outside-fileops.txt")["ok"]
        assert not op(Request.RENAME_ENTRY, path="keep.txt", name="a/b")["ok"]

    assert (tmp_path / "keep.txt").read_text() == "important"
    assert outside.exists()
    assert tmp_path.exists()


# --- multi-root explorer ----------------------------------------------------

def test_a_second_folder_is_browsable_but_never_widens_the_agent(app_client, tmp_path):
    """The user chose 'one active project'. Extra folders are for LOOKING —
    adding one must not move the boundary the agent's writes are checked
    against, which is still the session workspace."""
    proj = tmp_path / "proj"; proj.mkdir(); (proj / "a.py").write_text("a = 1\n")
    other = tmp_path / "other"; other.mkdir(); (other / "b.py").write_text("b = 2\n")

    client, engine = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE, "workspace": str(proj)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        _drain(ws, Event.FOLDERS)          # emitted by OPEN_WORKSPACE itself
        ws.send_text(json.dumps({"type": Request.ADD_FOLDER, "path": str(other)}))
        folders = _drain(ws, Event.FOLDERS)["data"]

        assert {r["name"] for r in folders["roots"]} == {"proj", "other"}
        assert folders["active"] == str(proj), "the agent's project must not move"

        # the second root is readable...
        ws.send_text(json.dumps({"type": Request.LIST_DIR, "session": sid,
                                 "root": str(other), "path": ""}))
        listing = _drain(ws, Event.DIR_LISTING)["data"]
        assert [e["name"] for e in listing["entries"]] == ["b.py"]

    # ...but the agent is still scoped to the active workspace only.
    assert str(engine.sessions[sid].workspace) == str(proj)


def test_browsing_an_unopened_folder_is_refused(app_client, tmp_path):
    proj = tmp_path / "proj"; proj.mkdir()
    secret = tmp_path / "secret"; secret.mkdir(); (secret / "k.txt").write_text("x")

    client, _ = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE, "workspace": str(proj)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        # never added via ADD_FOLDER, so the explorer may not look inside it
        ws.send_text(json.dumps({"type": Request.LIST_DIR, "session": sid,
                                 "root": str(secret), "path": ""}))
        got = _drain(ws, Event.DIR_LISTING)["data"]
    assert got["entries"] == [] and "not open" in got["error"]



def test_user_terminal_runs_commands_and_keeps_its_cwd(app_client, tmp_path):
    """The user's own shell — no agent, no approval. A persistent process, so
    `cd` survives between commands the way a real terminal does."""
    (tmp_path / "sub").mkdir()
    client, _ = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]

        ws.send_text(json.dumps({"type": Request.TERM_RUN, "session": sid,
                                 "command": "cd sub && pwd"}))
        seen = []
        for _ in range(120):
            ev = json.loads(ws.receive_text())
            if ev["type"] == Event.TERM_OUTPUT:
                seen.append(ev["data"])
                if ev["data"].get("done"):
                    break
        assert any("sub" in (d.get("text") or "") for d in seen)

        ws.send_text(json.dumps({"type": Request.TERM_RUN, "session": sid,
                                 "command": "pwd"}))
        after = []
        for _ in range(120):
            ev = json.loads(ws.receive_text())
            if ev["type"] == Event.TERM_OUTPUT:
                after.append(ev["data"])
                if ev["data"].get("done"):
                    break
    # cwd persisted into the second command — the point of a persistent shell
    assert any("sub" in (d.get("text") or "") and not d.get("echo") for d in after)


def _b64url(s: str) -> str:
    import base64
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_raw_serves_workspace_files_by_url_and_refuses_the_rest(app_client, tmp_path):
    client, engine = app_client
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text("<h1>hi</h1>")
    (tmp_path / "site" / "style.css").write_text("h1{color:red}")

    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        root = _b64url(str(tmp_path.resolve()))

        r = client.get(f"/raw/secret-token/{sid}/{root}/site/index.html")
        assert r.status_code == 200 and r.text == "<h1>hi</h1>"
        assert r.headers["content-type"].startswith("text/html")
        # A page's relative asset resolves to a sibling URL that still works.
        r = client.get(f"/raw/secret-token/{sid}/{root}/site/style.css")
        assert r.status_code == 200 and "text/css" in r.headers["content-type"]

        # Wrong token, unknown session, a path that escapes the root, and a
        # root the user never opened are all refused.
        assert client.get(f"/raw/nope/{sid}/{root}/site/index.html").status_code == 401
        assert client.get(f"/raw/secret-token/ghost/{root}/site/index.html").status_code == 404
        assert client.get(f"/raw/secret-token/{sid}/{root}/../outside.txt").status_code in (403, 404)
        bad_root = _b64url(str(tmp_path.parent.resolve()))
        assert client.get(f"/raw/secret-token/{sid}/{bad_root}/outside.txt").status_code == 403


def test_find_files_indexes_the_workspace_and_prunes_noise(app_client, tmp_path):
    client, engine = app_client
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "index.js").write_text("")
    (tmp_path / ".env.example").write_text("")

    with client.websocket_connect("/ws?token=secret-token") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE,
                                 "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        ws.send_text(json.dumps({"type": Request.FIND_FILES, "session": sid}))
        ev = _drain(ws, Event.FILE_INDEX)
        files = ev["data"]["files"]
        assert "src/app.py" in files
        assert ".env.example" in files
        assert not any(f.startswith("node_modules") for f in files)
        assert ev["data"]["truncated"] is False
