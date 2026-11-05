# Architecture

One process, one JSON file, one binary. The design goal is that a reviewer can read the
whole plugin in one sitting and know exactly what it can and cannot do.

## Data flow

```
Hermes tool call
      │
      ├─ post_tool_call(tool_name, args, result, task_id)
      │        │  sha256(canonical args) -> 64-bit digest      (args are not stored)
      │        │  cost table -> amount                          (state.json: costs, cached)
      │        │  tool name -> target id                        (state.json: targets, cached)
      │        ▼
      │   pending.jsonl  one compact JSON row appended per call
      │
      └─ zkagent_attest { max_amount, ttl_seconds }
               │  batch = oldest 8 pending rows
               │  refuse if total > max_amount or any amount > max_per_action
               │
               ├─ zkagent setup         keys/ (once; vk.bin + pk.bin, dir mode 700)
               ├─ zkagent policy        policy.json  (caps, expiry, chain, allow-list, signed)
               ├─ write receipt.json    totals + execution_id + zero trace_root
               ├─ write actions.json    the private witness: target, amount, digest
               ├─ zkagent prove         proof.json, and receipt.json gains the real trace_root
               ├─ zkagent verify --json verdict { accept | reject, reason_code }
               └─ compact pending.jsonl to the rows beyond the batch
```

`zkagent_verify` re-runs the last step on stored artifacts. `zkagent_disclose` reveals one
slot's digest plus the three sibling hashes on its path to the root, and nothing else.

## Files it owns

```
plugin-data/zkagent-attest/
  state.json          session header: cost table, target map, run history
  pending.jsonl       the ledger: one compact JSON row per tool call, append-only
  authority.hex       policy signing key, mode 600, created once
  keys/               circuit-specific proving/verifying keys, mode 700
  runs/run-<id>/      policy.json, receipt.json, actions.json, proof.json, disclosure-<i>.json
```

Nothing else is written. The plugin's own install directory is never used for state, so
`hermes plugins update` cannot lose it. `state.json` is the header of the session: if it is
gone, the log beside it is not a ledger, which is what keeps "clear `state.json` to start
fresh" true.

## Who reads it, and from where

Two surfaces read that directory and neither writes it:

- the four tools, in the agent turn;
- the Desktop pane, through `desktop/plugin.js` → `ctx.rest` → `dashboard/plugin_api.py`.

The pane cannot read the directory itself, and this is a property of the SDK, not an
oversight: `ctx.files` gives a plugin its own app-side data folder plus folders the user
grants through a picker, and refuses Hermes' own data outright, so `plugin-data/` is not
reachable from the renderer. The backend runs in the gateway process — beside the code that
wrote the files — and answers two GETs (`/latest`, `/disclosure?index=N`). It resolves the
data directory without creating it, reads `state.json` and `runs/run-<id>/`, and re-runs
`zkagent verify` (stateless: no `--seen` file) to publish the `reason_code` the run header
does not keep. A run directory recorded in `state.json` is only used when it really sits
under this plugin's `runs/`, so a doctored header cannot turn the route into a file reader.

## The cost of the hook

`post_tool_call` runs synchronously on every tool call the agent makes, so its per-call cost
is latency on the user's critical path. Two rules keep it small:

- the hook **appends** one line; it never reads or rewrites the ledger. `state.json` changes
  only when the cost table, the target map or the run history changes — i.e. once for the
  header and at most once per tool name — and the cost table and target map are cached
  against its `(mtime, size, inode)` signature rather than re-read.
- the digest is computed once, from one serialisation of the arguments. It cannot be cached
  and it does not shrink: `sha256` over a 200 KB payload is ~0.8 ms on its own, and that is
  the floor for a large call.

Measured on this laptop with `python3 scripts/bench.py --repeat 7` (200 calls, median):

| argument payload | before | after |
|---|---|---|
| 200 B | 0.97 ms/call | 0.050 ms/call |
| 200 KB | 2.00 ms/call | 0.86 ms/call |

Disk after 200 calls: 28.2 KiB in one file before, 18.5 KiB split across `state.json` and
`pending.jsonl` after. Re-measure rather than trusting the table; a loaded machine moves a
single run by tens of percent.

## Why the receipt is generated here

zkagent's CLI takes a receipt as input and rewrites its `trace_root` during `prove`. The
plugin therefore writes the public statement it can defend (totals, count, time, chain,
execution id) and lets the binary fill in the commitment it computes from the witness. A
pre-filled root would have to match the witness exactly or verification fails, and writing
it in Python would duplicate the MiMC tree for no benefit.

## Failure modes, and what the caller sees

| Situation | Result |
|---|---|
| no pending actions | `{"ok": false, "error": "nothing to attest yet..."}` |
| session total above the requested cap | refusal naming both numbers; nothing signed |
| an action above `max_per_action` | refusal |
| binary missing | `zkagent_status.error` with the install instruction; other tools refuse |
| `prove` fails (bad witness, format drift) | `{"ok": false, "stage": "prove", ...}` |
| verify rejects | `ok: false`, `verdict: "reject"`, `reason_code` preserved |
| the hook itself throws | swallowed; the tool call is unaffected |

## What is deliberately absent

- No daemon, no queue, no background thread, no timer flush. A write to the ledger is one
  `O_APPEND` write per call, so a crash or a reloaded module loses at most a torn last line,
  and a flush that has not happened yet can never hide a recorded action.
- No database. The ledger is an append-only log, compacted at attest time, because the
  session is small by design (8 actions per receipt) and a reviewer can read it with `tail`.
- No cache of the binary's version beyond a call to `zkagent version` on status.
- No Windows-specific path handling: `plugin_data_dir()` resolves the profile home, so the
  same code runs on all three platforms.
