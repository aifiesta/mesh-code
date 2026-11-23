"""Exchange rates for showing costs in another currency.

The gateway bills in US dollars and the app keeps every figure in dollars.
This module only finds a rate for DISPLAY, so the UI can show "≈ ₱0.045"
next to a bill that was $0.0007.

Order, per refresh:
  1. Frankfurter (central-bank reference rates, open source, no key)
  2. fawazahmed0 currency API (CC0, daily), on jsDelivr and then its mirror
  3. the last rate saved in ~/.mesh-code/fx.json, with its date
  4. nothing: the UI shows dollars

What a rate service returns is someone else's data: parsed strictly (a
positive number in a sane range for that currency), size-capped, and any
failure is swallowed. Nothing is sent but the request for USD rates — no
key, no user data. A refresh happens at most once a day and never inside a
turn, so a slow or missing service can only delay the rate, never a reply.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from typing import Awaitable, Callable

import httpx

from . import config as config_mod

# Display currencies. Adding one is one line: code -> (symbol, label, the
# range a USD rate for it must fall in to be believed).
CURRENCIES: dict[str, dict] = {
    "USD": {"symbol": "$", "label": "US dollar", "sane": (1.0, 1.0)},
    "PHP": {"symbol": "₱", "label": "Philippine peso", "sane": (10.0, 500.0)},
}

MAX_AGE = 24 * 3600          # refresh a saved rate at most once a day
RETRY_AFTER = 15 * 60        # after every service failed, wait before trying again
TIMEOUT = 4.0                # per service, whole request
MAX_BYTES = 512 * 1024       # the fawazahmed0 file is ~15 KB; refuse anything huge
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FX_FILE_NAME = "fx.json"


def _fx_file():
    # Read CONFIG_DIR at call time so tests that point it elsewhere are honoured.
    return config_mod.CONFIG_DIR / FX_FILE_NAME


# ---- parsing: one function per service, each returns (rate, date) or raises

def _sane(code: str, rate) -> float:
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise ValueError("rate is not a number")
    rate = float(rate)
    lo, hi = CURRENCIES[code]["sane"]
    if not math.isfinite(rate) or rate <= 0 or not lo <= rate <= hi:
        raise ValueError(f"rate {rate!r} out of range for {code}")
    return rate


def _date(v) -> str | None:
    return v if isinstance(v, str) and _DATE.match(v) else None


def parse_frankfurter(data, code: str) -> tuple[float, str | None]:
    if not isinstance(data, dict) or data.get("base") != "USD":
        raise ValueError("not a USD rate table")
    rates = data.get("rates")
    if not isinstance(rates, dict):
        raise ValueError("no rates")
    return _sane(code, rates.get(code)), _date(data.get("date"))


def parse_fawaz(data, code: str) -> tuple[float, str | None]:
    if not isinstance(data, dict) or not isinstance(data.get("usd"), dict):
        raise ValueError("not a USD rate table")
    return _sane(code, data["usd"].get(code.lower())), _date(data.get("date"))


# name shown to the user, url builder, parser
SOURCES: list[tuple[str, Callable[[str], str], Callable]] = [
    ("Frankfurter",
     lambda c: f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={c}",
     parse_frankfurter),
    ("fawazahmed0",
     lambda c: "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.min.json",
     parse_fawaz),
    # The project's own documented mirror for when jsDelivr fails.
    ("fawazahmed0",
     lambda c: "https://latest.currency-api.pages.dev/v1/currencies/usd.min.json",
     parse_fawaz),
]


async def _fetch_one(client: httpx.AsyncClient, url: str, parse, code: str):
    async def go():
        r = await client.get(url, headers={"Accept": "application/json"})
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        body = r.content
        if len(body) > MAX_BYTES:
            raise ValueError("reply too large")
        return parse(json.loads(body), code)
    # wait_for bounds the WHOLE request, including a server that trickles bytes.
    return await asyncio.wait_for(go(), TIMEOUT)


async def fetch_rate(code: str, client: httpx.AsyncClient | None = None,
                     sources=None) -> dict | None:
    """Try each service in order. The first good answer wins; None if all fail."""
    sources = SOURCES if sources is None else sources
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False,
                                   headers={"User-Agent": "mesh-code"})
    try:
        for name, url_for, parse in sources:
            try:
                rate, date = await _fetch_one(client, url_for(code), parse, code)
            except Exception:
                continue
            return {"rate": rate, "date": date, "source": name,
                    "fetched_at": time.time()}
        return None
    finally:
        if own:
            await client.aclose()


# ---- saved rates -------------------------------------------------------------

def load_saved() -> dict:
    try:
        data = json.loads(_fx_file().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _saved_entry(code: str) -> dict | None:
    e = load_saved().get(code)
    if not isinstance(e, dict):
        return None
    try:
        rate = _sane(code, e.get("rate"))
    except (ValueError, KeyError):
        return None                     # a hand-edited or corrupt file is ignored
    fetched = e.get("fetched_at")
    return {"rate": rate, "date": _date(e.get("date")),
            "source": e.get("source") if isinstance(e.get("source"), str) else "",
            "fetched_at": float(fetched) if isinstance(fetched, (int, float)) else 0.0}


def save_entry(code: str, entry: dict) -> None:
    """Atomic, best-effort: a failed write only means the next launch refetches."""
    try:
        data = load_saved()
        data[code] = entry
        config_mod._secure_dir(config_mod.CONFIG_DIR)
        path = _fx_file()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
        config_mod.secure_file(path)
    except (OSError, TypeError, AttributeError):
        pass


# ---- what the UI is told -----------------------------------------------------

def info(code: str, entry: dict | None, *, stale: bool = False) -> dict:
    """The payload the UI formats every amount with.

    rate None means "show dollars": either dollars were chosen, or no rate
    has ever been fetched. stale means the services failed just now and this
    is the saved rate (or, with rate None, that there is none to fall back on).
    """
    meta = CURRENCIES.get(code) or CURRENCIES["USD"]
    if code == "USD" or code not in CURRENCIES:
        return {"currency": "USD", "symbol": "$", "rate": None}
    out = {"currency": code, "symbol": meta["symbol"], "label": meta["label"],
           "rate": None, "date": None, "source": None, "stale": stale}
    if entry:
        out.update(rate=entry["rate"], date=entry.get("date"),
                   source=entry.get("source") or None, stale=stale,
                   fetched_at=entry.get("fetched_at"))
    return out


class RateKeeper:
    """Holds the current display rate and refreshes it in the background."""

    def __init__(self, fetch: Callable[[str], Awaitable[dict | None]] = fetch_rate,
                 clock: Callable[[], float] = time.time):
        self._fetch = fetch
        self._clock = clock
        self._failed_at: dict[str, float] = {}
        self._stale: dict[str, bool] = {}
        self._busy: set[str] = set()

    def current(self, code: str) -> dict:
        return info(code, _saved_entry(code) if code != "USD" else None,
                    stale=self._stale.get(code, False))

    def due(self, code: str) -> bool:
        if code == "USD" or code not in CURRENCIES or code in self._busy:
            return False
        now = self._clock()
        if now - self._failed_at.get(code, -1e18) < RETRY_AFTER:
            return False
        e = _saved_entry(code)
        return e is None or now - e["fetched_at"] >= MAX_AGE

    async def refresh(self, code: str) -> dict | None:
        """Fetch if due. Returns the new payload after a fetch, else None."""
        if not self.due(code):
            return None
        self._busy.add(code)
        try:
            entry = await self._fetch(code)
        except Exception:
            entry = None
        finally:
            self._busy.discard(code)
        if entry:
            entry = {**entry, "fetched_at": self._clock()}
            save_entry(code, entry)
            self._failed_at.pop(code, None)
            self._stale[code] = False
        else:
            self._failed_at[code] = self._clock()
            self._stale[code] = True
        return self.current(code)
