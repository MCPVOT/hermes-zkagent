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
               ├─ compact pending.jsonl to the rows beyond the batch
               └─ session.json          rebuilt: ordered receipts, per-receipt totals, total, Merkle root
```

`zkagent_verify` re-runs the last step on stored artifacts. `zkagent_disclose` reveals one
slot's digest plus the three sibling hashes on its path to the root, and nothing else.

## Files it owns

```
plugin-data/zkagent-attest/
  state.json          session header: cost table, target map, run history
  pending.jsonl       the ledger: one compact JSON row per tool call, append-only
  session.json        the index over every receipt: ordered ids, roots, totals, Merkle root
  seen.json           the replay guard: execution ids and session roots already accepted
  authority.hex       policy signing key, mode 600, created once
  keys/               circuit-specific proving/verifying keys, mode 700
  runs/run-<id>/      policy.json, receipt.json, actions.json, proof.json, disclosure-<i>.json
```

Nothing else is written. The plugin's own install directory is never used for state, so
`hermes plugins update` cannot lose it. `state.json` is the header of the session: if it is
gone, the log beside it is not a ledger, which is what keeps "clear `state.json` to start
fresh" true.

## The session index

One receipt covers at most 8 actions, so a session is several receipts. `session.json` is the
claim "these are the receipts, in this order, and together they are the session". It is
rebuilt from the run history after every attestation, and every field in it is either copied
from a receipt file or recomputed from those receipts:

```json
{"version": 1, "updated_at": …, "receipt_count": 2, "action_count": 9, "total": 17,
 "root": "<merkle>",
 "merkle": {"hash": "sha256", "leaf": "sha256(trace_root bytes)", "node": "sha256(left || right)",
            "odd": "promoted unchanged", "empty": "00…00", "order": "ascending execution_id"},
 "receipts": [{"execution_id": 1001, "trace_root": "…", "total": 9, "actions": 5,
               "receipt": "runs/run-1001/receipt.json"}, …]}
```

The Merkle construction, for a verifier re-implementing it:

- leaf = `sha256(bytes.fromhex(trace_root))` — the receipt's 32-byte root
- node = `sha256(left ‖ right)`, the two children's 32-byte digests, in order
- an odd node is promoted unchanged to the next level: no duplication, no padding leaf
- one leaf is the root; no leaves is 64 hex zeros
- leaves are taken in ascending `execution_id` order, and the list is checked to be in that
  order, so reordering the receipts changes the root

No signature: the security comes from the receipts, and the index cannot manufacture one.
`zkagent_verify` therefore treats the index as a claim to be checked, not data to be trusted:

| check | failure |
|---|---|
| each listed `trace_root` equals the `trace_root` in the receipt file it names | `session_mismatch` |
| each listed `total` / `actions` equals the receipt's `total_amount` / `action_count` | `session_mismatch` |
| the listed execution id equals the receipt's `execution_id` | `session_mismatch` |
| the sum of the receipts equals the session `total` (and `action_count`) | `session_mismatch` |
| the stored `root` equals the root recomputed from the listed order | `session_mismatch` |
| every receipt accepts when the binary verifies it (`{"session": true}`) | `reject` |

Any one of those is a refusal, never a warning, and none of them is recorded as an
acceptance. A receipt-level verify also requires that the receipt is listed in the index: a
receipt that accepts alone but is missing from (or disagrees with) the index is
`session_mismatch`, because it is not part of the attested session.

## The replay guard

`seen.json` is one record per acceptance:

```json
{"version": 1,
 "executions": {"1001": {"accepted_at": 1793898692, "trace_root": "…", "total": 9,
                          "actions": 5, "receipt": "runs/run-1001/receipt.json"}},
 "sessions":   {"<merkle root>": {"accepted_at": …, "executions": [1001, 1002], "total": 17,
                                  "receipt_count": 2, "receipt": "session.json"}}}
```

`zkagent_verify` refuses before it runs the binary if the id (or root) is already there, and
records only on accept. The guard is read-modify-write with an atomic replace; two verifies
racing could in principle lose one record, which is why the format is one small file and not
a log — a lost record costs a replay check, not evidence. It is local by design: it sees this
profile's acceptances, not the world's. Execution ids are never reused — a session started
over a cleared `state.json` continues above the highest accepted id, and an explicitly
requested id that is spent is refused rather than renumbered.


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
| binary missing | the pinned installer runs once; `install` in the result says what it did |
| binary missing and the install failed | `zkagent_status.install` with the installer's own output, plus `error` naming both remedies; other tools refuse |
| `prove` fails (bad witness, format drift) | `{"ok": false, "stage": "prove", ...}` |
| verify rejects | `ok: false`, `verdict: "reject"`, `reason_code` preserved |
| `session.json` disagrees with the receipts | `ok: false`, `verdict: "session_mismatch"`, the problems listed; nothing recorded |
| a receipt accepts but the index omits it | `ok: false`, `verdict: "session_mismatch"`, `reason: "receipt_not_indexed"` |
| an execution id (or session root) already accepted | `ok: false`, `verdict: "replay"`, `reason: "already_accepted"`, `accepted_at` |
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
