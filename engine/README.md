# mesh-code-engine

The headless agent engine behind [Mesh Code](../README.md): turns, hops,
tool dispatch, routing, repo memory and the safety gates, with a local
WebSocket transport and no terminal anywhere in it.

Packaged separately from the desktop app so it can be frozen into a
standalone binary (see `../packaging/engine.spec`) — that freeze is what
makes Mesh Code installable on a machine with no Python at all.

```bash
pip install -e ".[test]"
python -m pytest          # 26 tests, run against a local SSE gateway
python -m meshharness     # start the engine; prints {ready, port, token}
```

See [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the event
protocol and why the loop is synchronous.
