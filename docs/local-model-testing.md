# Testing the local model layer

Manual scenarios for the local model layer (install, fallback, recovery,
the tier gate), then escalation and the budget.

Logs: Help › Show Local Model Logs, or `~/.mesh-code/local/logs/llama-server.log`.

## 1. First local turn

- Open a project, ask: "list the files here and summarise the layout".
- Expect: pill reads `Local · <the model for this machine>` (on 16–18 GB,
  `qwen3.5-9b-q4`) with a green `local` tag. The
  first reply is slow once (weights load), later replies are fast. The turn
  footer prints `free · local`; session cost stays at $0.0000.

## 2. Tool calling on a 9B model

- Ask for a real change: "add a `--verbose` flag to the CLI and run the tests".
- Expect: `write_file` / `run_bash` cards as normal. A call the model printed
  as text still becomes a card, with a repair notice in the transcript.
- A reply that only *describes* the change instead of making it is the
  known small-model failure. Note how often it happens.

## 3. Fallback to the cloud

- Settings › Local model › **Stop**, then switch **Local model** off, send a prompt.
- Expect: one notice "local models are turned off in Settings — using
  <your cloud fallback model>"; the turn runs on the cloud and the footer
  shows a dollar amount.
- Switch it back on, send again: local resumes.
- Also: layer on, server stopped, send a prompt → the server restarts on its own.

## 4. Crash recovery

- During a local turn run `pkill llama-server` in a terminal.
- Expect: the hop retries after a restart, or the turn ends with a notice
  and the next prompt works. Settings › Local model shows the restart count.

## 5. Interrupt during load

- Stop the server, send a prompt, press **Stop** while "Starting the local
  model…" is shown.
- Expect: the turn stops cleanly; no stuck "working" state.

## 6. Settings that need a restart

- Change **Context** to 64k, or **GPU** to off.
- Expect: "restart to apply new settings" next to the server state until
  **Restart** is pressed. GPU off makes the model CPU-only — a useful speed
  comparison.

## 7. Delete while loaded

- Settings › Local model: **Delete** on the running model is disabled
  ("Stop the server first"). Stop, then Delete works and the card offers
  the download again.

## 8. Cloud pin and back

- Model popover › **Change model** → a cloud model → run a turn → pick
  `local/auto` again.
- Expect: prices on the cloud row, `free · on this machine` on the local
  one. Local rows have no **Never** button.

## 9. No key, local only

- Palette › **Replace API key** › Clear.
- Expect: the key gate shows "Continue with the local model only".
  Continue, run a prompt: it works locally. Turning the local layer off here
  leaves nothing to answer — expect a notice, not a hang. Re-enter the key.

## 10. Quit and orphans

- Quit the app, then `pgrep -fl llama-server` → nothing.
- Relaunch: the model is detected as installed; the pill is ready with no download.

## 11. Tier gate script

Quit the app first (the script refuses to run beside a live server).

From the repository root:

```bash
engine/.venv/bin/python engine/scripts/local_smoke.py --cwd .
```

Expect six lines, each `ok` with `structured` calls, TTFT and tok/s, then
`PASS`. Any `text-parsed` or `NO CALL` line is evidence against the tier default.

## 12. Long sessions

- Keep one chat going past 32k context.
- Expect "compacted history" notices instead of a 400 error: the limit comes
  from the running context, not the cloud catalog.

## 13. Download lifecycle (already exercised once)

- Cancel mid-download → the `.part` file stays; Install again resumes from it.
- Kill Wi-Fi mid-download → a warn notice "… download failed: …" appears;
  Install again resumes.
- The bar above the transcript shows rate, ETA and "cloud models work meanwhile".

## 14. Escalate by hand

- During a local turn press **Escalate**.
- Expect: "escalating at the next step", the pill reads `Escalated ·
  claude-sonnet-5.5`, and the next hop runs in the cloud with a dollar cost.
  After 8 hops or 300k tokens a notice says it handed back, and the next
  turn starts on the local model again.

## 15. Escalation on evidence

- Give the local model a task it cannot finish (a wide refactor across many
  files) and let it run.
- Expect: after a weak reply, two failed batches of tool calls, or a stall,
  a notice names the trigger and the strong model takes over once per turn.
  Settings › Policy › **Escalate on its own** off stops this.

## 16. Daily and monthly holds

- Settings › Budget: set the monthly limit to this month's spend (from the
  Usage popover) plus $0.05, pin a cloud model, run a turn.
- Expect: the ring turns red, the turn ends with "cloud hop held: today's
  share … is used up", and the Usage popover offers **Allow more today**.
  Press it and run again: the turn goes through. On local first, the same
  hold sends an escalated turn back to the local model.
- Put the limit back to $10.

## 17. The key's own cap

- Hard to trigger for real; `test_a_402_sets_the_key_cap_hold_until_cleared`
  covers it. With a key whose cap is reached, expect a key-cap hold, a full
  red ring, and **I raised the cap** in the Usage popover.
