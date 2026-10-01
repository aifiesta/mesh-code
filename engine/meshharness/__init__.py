"""Mesh Harness — the Mesh agent as a desktop application.

Layers, outermost first:

    desktop/     Electron shell (window, menus, installer)
    ui/          React frontend — draws events, sends requests
    server.py    local WebSocket transport, one connection per window
    session.py   the headless agent loop: turns, hops, tool dispatch
    core/        the vendored engine (streaming, routing, memory, safety)

The rule that keeps this honest: `core` and `session` never draw anything
and never read stdin. Everything a user would see leaves as an event and
everything a user decides arrives as a request.
"""

from .core import __version__

__all__ = ["__version__"]
