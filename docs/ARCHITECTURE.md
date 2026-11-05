# Architecture

One process, one JSON file, one binary. The design goal is that a reviewer can read the
whole plugin in one sitting and know exactly what it can and cannot do.

## Data flow

```
Hermes tool call
      │
      ├─ post_tool_call(tool_name, args, result, task_id)
      │        │  sha256(canonical args) -> 64-bit digest      (args are not stored)
      │        │  cost table -> amount                          (state.json: costs)
      │        │  tool name -> target id                        (state.json: targets)
      │        ▼
      │   state.json  { pending: [action...], runs: [...], targets, costs, next_run_id }
      │
      └─ zkagent_attest { max_amount, ttl_seconds }
               │  batch = oldest 8 pending
               │  refuse if total > max_amount or any amount > max_per_action
               │
               ├─ zkagent setup         keys/ (once; vk.bin + pk.bin, dir mode 700)
               ├─ zkagent policy        policy.json  (caps, expiry, chain, allow-list, signed)
               ├─ write receipt.json    totals + execution_id + zero trace_root
               ├─ write actions.json    the private witness: target, amount, digest
               ├─ zkagent prove         proof.json, and receipt.json gains the real trace_root
               └─ zkagent verify --json verdict { accept | reject, reason_code }
```

`zkagent_verify` re-runs the last step on stored artifacts. `zkagent_disclose` reveals one
slot's digest plus the three sibling hashes on its path to the root, and nothing else.

## Files it owns

```
plugin-data/zkagent-attest/
  state.json          ledger, cost table, target map, run history
  authority.hex       policy signing key, mode 600, created once
  keys/               circuit-specific proving/verifying keys, mode 700
  runs/run-<id>/      policy.json, receipt.json, actions.json, proof.json, disclosure-<i>.json
```

Nothing else is written. The plugin's own install directory is never used for state, so
`hermes plugins update` cannot lose it.

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

- No daemon, no queue, no background thread. State is a JSON file rewritten atomically.
- No database. The ledger is append-in-memory and rewritten; sessions are small by design
  (8 actions per receipt).
- No cache of the binary's version beyond a call to `zkagent version` on status.
- No Windows-specific path handling: `plugin_data_dir()` resolves the profile home, so the
  same code runs on all three platforms.
