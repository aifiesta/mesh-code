"""Vendored Mesh engine — the CLI's proven core, minus its terminal.

These modules are lifted from the meshapi CLI and deliberately kept close
to the original so fixes can flow between the two. What changed, and why:

  safety.py   the auto-approval gates and path resolution take an explicit
              workspace `root`. The CLI could ask os.getcwd() because one
              process served one directory; the harness serves many
              workspaces from one engine.
  tools.py    execute() and build_system_prompt() take that same `root`.
              Prompt text that described a terminal now describes a window.
  config.py   its own config home (~/.mesh-harness), reads the CLI's
              credentials file so one login covers both, and raises instead
              of sys.exit() on a bad base_url — this process serves every
              open window and must not die over one setting.
  plan.py     no longer draws itself with rich; it serializes via to_dict()
              and the frontend renders it.

Everything else — the streaming client and its tool-call repair, the
routing table and cohort classifier, repo memory, compaction, the optimize
levers, loop/stall guards — is byte-for-byte the CLI's.
"""

__version__ = "0.1.4"
