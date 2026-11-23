"""Display currency: two rate services, a saved rate, then dollars.

The rate services are faked with httpx.MockTransport, so no test touches
the network. Each fake records what it was asked for, which is how the
tests prove dollars never makes a request and nothing but a rate request
is ever sent.
"""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from meshharness.core import fx
from meshharness.protocol import Event, Request
from meshharness.server import Engine, create_app

FRANK = {"amount": 1.0, "base": "USD", "date": "2026-11-20", "rates": {"PHP": 63.777}}
FAWAZ = {"date": "2026-11-23", "usd": {"php": 63.83, "eur": 0.86}}


class FakeRates:
    """One fake per service host. `mode[host]` says how it answers."""

    def __init__(self, **mode):
        self.mode = {"api.frankfurter.dev": "ok", "cdn.jsdelivr.net": "ok",
                     "latest.currency-api.pages.dev": "ok", **mode}
        self.seen: list[httpx.Request] = []

    async def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        m = self.mode.get(req.url.host, "down")
        if m == "down":
            raise httpx.ConnectError("unreachable", request=req)
        if m == "slow":
            await asyncio.sleep(30)
        if m == "500":
            return httpx.Response(500, text="oops")
        if m == "html":
            return httpx.Response(200, text="<html>captive portal</html>")
        if m == "nonsense":
            body = ({**FRANK, "rates": {"PHP": -4}} if "frankfurter" in req.url.host
                    else {**FAWAZ, "usd": {"php": "63.8 pesos"}})
            return httpx.Response(200, json=body)
        if m == "huge":
            return httpx.Response(200, content=b" " * (fx.MAX_BYTES + 1))
        return httpx.Response(200, json=FRANK if "frankfurter" in req.url.host else FAWAZ)

    def hosts(self):
        return [r.url.host for r in self.seen]


def run(coro):
    return asyncio.run(coro)


async def fetch_with(fake, code="PHP"):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c:
        return await fx.fetch_rate(code, client=c)


@pytest.fixture
def fast_timeout(monkeypatch):
    monkeypatch.setattr(fx, "TIMEOUT", 0.2)


# ---- the order of services ------------------------------------------------

def test_first_service_answers():
    fake = FakeRates()
    got = run(fetch_with(fake))
    assert got["rate"] == 63.777 and got["source"] == "Frankfurter"
    assert got["date"] == "2026-11-20"
    assert fake.hosts() == ["api.frankfurter.dev"]


def test_first_fails_second_answers():
    fake = FakeRates(**{"api.frankfurter.dev": "500"})
    got = run(fetch_with(fake))
    assert got["rate"] == 63.83 and got["source"] == "fawazahmed0"
    assert fake.hosts() == ["api.frankfurter.dev", "cdn.jsdelivr.net"]


def test_second_service_falls_back_to_its_own_mirror():
    fake = FakeRates(**{"api.frankfurter.dev": "down", "cdn.jsdelivr.net": "down"})
    got = run(fetch_with(fake))
    assert got["source"] == "fawazahmed0"
    assert fake.hosts()[-1] == "latest.currency-api.pages.dev"


def test_all_down_gives_none():
    fake = FakeRates(**{h: "down" for h in ("api.frankfurter.dev", "cdn.jsdelivr.net",
                                            "latest.currency-api.pages.dev")})
    assert run(fetch_with(fake)) is None


def test_slow_service_is_cut_off_and_the_next_one_used(fast_timeout):
    fake = FakeRates(**{"api.frankfurter.dev": "slow"})
    got = run(asyncio.wait_for(fetch_with(fake), 5))
    assert got["source"] == "fawazahmed0"


@pytest.mark.parametrize("bad", ["nonsense", "html", "huge"])
def test_a_bad_reply_is_ignored_not_believed(bad):
    fake = FakeRates(**{"api.frankfurter.dev": bad, "cdn.jsdelivr.net": bad,
                        "latest.currency-api.pages.dev": bad})
    assert run(fetch_with(fake)) is None


def test_only_a_rate_request_is_sent():
    fake = FakeRates(**{"api.frankfurter.dev": "500"})
    run(fetch_with(fake))
    for r in fake.seen:
        assert r.method == "GET" and r.content == b""
        assert "authorization" not in {k.lower() for k in r.headers}


# ---- strict parsing -----------------------------------------------------------

@pytest.mark.parametrize("rate", [0, -1, 5, 9999, float("nan"), float("inf"), True, "63", None])
def test_out_of_range_or_wrong_type_rates_are_refused(rate):
    with pytest.raises(ValueError):
        fx.parse_frankfurter({"base": "USD", "rates": {"PHP": rate}}, "PHP")


def test_a_bad_date_is_dropped_but_the_rate_kept():
    rate, date = fx.parse_fawaz({"date": "<script>", "usd": {"php": 60}}, "PHP")
    assert rate == 60 and date is None


def test_a_table_for_another_base_is_refused():
    with pytest.raises(ValueError):
        fx.parse_frankfurter({"base": "EUR", "rates": {"PHP": 63}}, "PHP")


# ---- the keeper: saved rate, then dollars --------------------------------------

class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def keeper_with(fake, clock=None):
    async def fetch(code):
        return await fetch_with(fake, code)
    return fx.RateKeeper(fetch=fetch, clock=clock or Clock())


ALL_DOWN = {h: "down" for h in ("api.frankfurter.dev", "cdn.jsdelivr.net",
                                "latest.currency-api.pages.dev")}


def test_dollars_never_asks_anyone():
    fake = FakeRates()
    k = keeper_with(fake)
    assert run(k.refresh("USD")) is None
    assert k.current("USD") == {"currency": "USD", "symbol": "$", "rate": None}
    assert fake.seen == []


def test_a_fetched_rate_is_saved_and_used_without_refetching():
    fake, clock = FakeRates(), Clock()
    k = keeper_with(fake, clock)
    got = run(k.refresh("PHP"))
    assert got["rate"] == 63.777 and got["stale"] is False and got["symbol"] == "₱"
    # Same day: no second request.
    clock.t += 3600
    assert run(k.refresh("PHP")) is None
    assert len(fake.seen) == 1
    # A day later: refreshed.
    clock.t += fx.MAX_AGE
    run(k.refresh("PHP"))
    assert len(fake.seen) == 2


def test_both_fail_with_a_saved_rate_uses_it_and_says_so():
    good, clock = FakeRates(), Clock()
    run(keeper_with(good, clock).refresh("PHP"))
    clock.t += fx.MAX_AGE + 1
    k = keeper_with(FakeRates(**ALL_DOWN), clock)
    got = run(k.refresh("PHP"))
    assert got["rate"] == 63.777 and got["stale"] is True
    assert got["date"] == "2026-11-20" and got["source"] == "Frankfurter"


def test_both_fail_with_no_saved_rate_shows_dollars():
    fake = FakeRates(**ALL_DOWN)
    k = keeper_with(fake)
    got = run(k.refresh("PHP"))
    assert got["currency"] == "PHP" and got["rate"] is None and got["stale"] is True
    # And it does not hammer the services: the next try waits.
    n = len(fake.seen)
    assert run(k.refresh("PHP")) is None
    assert len(fake.seen) == n


def test_a_corrupt_saved_file_is_ignored(isolate_config_home):
    (isolate_config_home / "fx.json").write_text(json.dumps({"PHP": {"rate": "lots"}}))
    assert fx.RateKeeper().current("PHP")["rate"] is None
    (isolate_config_home / "fx.json").write_text("not json")
    assert fx.RateKeeper().current("PHP")["rate"] is None


def test_a_fetch_that_raises_never_escapes():
    async def boom(code):
        raise RuntimeError("bad")
    got = run(fx.RateKeeper(fetch=boom).refresh("PHP"))
    assert got["rate"] is None


# ---- over the socket ------------------------------------------------------------

@pytest.fixture
def app_client(gateway, monkeypatch):
    monkeypatch.setenv("MESHAPI_BASE_URL", gateway.base_url)
    monkeypatch.setenv("MESHAPI_API_KEY", "rsk_test")
    calls = []

    async def fake_fetch(code):
        calls.append(code)
        return {"rate": 63.5, "date": "2026-11-20", "source": "Frankfurter",
                "fetched_at": 1.0e12}
    engine = Engine(token="t")
    engine.fx = fx.RateKeeper(fetch=fake_fetch)
    with TestClient(create_app(engine)) as client:
        yield client, engine, calls


def _drain(ws, want, limit=200):
    for _ in range(limit):
        ev = json.loads(ws.receive_text())
        if ev.get("type") == want:
            return ev
    raise AssertionError(f"never saw {want}")


def test_default_is_dollars_and_no_rate_is_fetched(app_client):
    client, engine, calls = app_client
    with client.websocket_connect("/ws?token=t") as ws:
        ready = _drain(ws, "engine.ready")
    assert ready["data"]["fx"]["currency"] == "USD"
    setting = {s["key"]: s for s in ready["data"]["settings"]["settings"]}["currency"]
    assert setting["value"] == "USD"
    assert [o["value"] for o in setting["options"]] == ["USD", "PHP"]
    assert calls == []


def test_choosing_pesos_fetches_a_rate_and_tells_the_window(app_client):
    client, engine, calls = app_client
    with client.websocket_connect("/ws?token=t") as ws:
        _drain(ws, "engine.ready")
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "key": "currency",
                                 "value": "PHP"}))
        first = _drain(ws, Event.FX)
        assert first["data"]["currency"] == "PHP"
        got = first if first["data"]["rate"] else _drain(ws, Event.FX)
    assert got["data"]["rate"] == 63.5 and got["data"]["symbol"] == "₱"
    assert calls == ["PHP"]


def test_an_unknown_currency_is_refused(app_client):
    client, engine, calls = app_client
    with client.websocket_connect("/ws?token=t") as ws:
        _drain(ws, "engine.ready")
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "key": "currency",
                                 "value": "XYZ"}))
        err = _drain(ws, Event.CONFIG_ERROR)
    assert err["data"]["key"] == "currency"
    assert engine.cfg.get("currency") == "USD"
