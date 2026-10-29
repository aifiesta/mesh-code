"""Vendored Mesh engine — the CLI's proven core, minus its terminal.

These modules are lifted from the meshapi CLI and deliberately kept close
to the original so fixes can flow between the two. What changed, and why:

  safety.py   the auto-approval gates and path resolution take an explicit
              workspace `root`. The CLI could ask os.getcwd() because one
              process served one directory; the harness serves many
              workspaces from one engine.
  tools.py    execute() and build_system_prompt() take that same `root`.
              Prompt text that described a terminal now describes a window.
  config.py   its own config home (~/.mesh-code), reads the CLI's
              credentials file so one login covers both, and raises instead
              of sys.exit() on a bad base_url — this process serves every
              open window and must not die over one setting.
  plan.py     no longer draws itself with rich; it serializes via to_dict()
              and the frontend renders it.

  client.py   one request per hop, aimed at a per-hop Target (local or
              cloud); the CLI's optimized-attempt retry is gone with its
              optimize levers.
  memory.py   dedupe_read() no longer consults a pruning dial.

loopguard.py and styles.py are the CLI's byte-for-byte; the tool-call repair
is unchanged inside client.py. compact.py and pricing.py carry fixes made
here that have not flowed back to the CLI yet. The CLI's local routing table
is not here: Mesh's own auto routing does that job.
"""

__version__ = "0.3.0"
