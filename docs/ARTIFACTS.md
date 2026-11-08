# On-disk artifacts

What the plugin writes, and what a later version of it must still be able to read. The shape lines
below are checked against a live run by `scripts/check_artifacts.py`, so this file cannot drift from
the code that produces it.

Everything lives under `plugin-data/zkagent-attest/`.

## `state.json` — the session header

```
keys: guard, next_run, next_run_id, runs, targets
keys.runs[]: actions, at, cap, dir, reason, run_id, tools, total, trace_root, verdict
```

`guard` is the replay header: how many executions were accepted, the highest id, and a digest of the
set. A state file whose `guard` disagrees with `seen.json` is refused rather than reset.

## `seen.json` — the replay ledger

```
keys: executions, sessions, version
keys.executions[]: accepted_at, actions, receipt, total, trace_root
keys.sessions[]: accepted_at, executions, receipt, receipt_count, total
```

One namespace, two sections. An execution claimed under either blocks the other.

## `session.json` — the index across receipts

```
keys: action_count, merkle, receipt_count, receipts, root, total, updated_at, version
keys.receipts[]: actions, execution_id, receipt, total, trace_root
```

The root is a Merkle root over the receipts in order; `total` must equal the sum of the receipt
totals, and `verify` refuses an index that disagrees with the artifacts on disk.

## `runs/run-N/` — one attestation

```
actions.json    the witness: target, amount, trace_digest per action. Never publish with a receipt
policy.json     signed policy: allowed_targets, chain_id, expires_at, max_amount, max_per_action
receipt.json    action_count, chain_id, completed_at, execution_id, total_amount, trace_root
proof.json      format_version, proof, public_inputs
```

`actions.json` is the one file here that must never leave the machine with the bundle: it reveals
every target and amount.

## Deliberately not versioned

`pending.jsonl` is append-only and rewritten on compaction; its line shape is an implementation
detail. The ledger row format may change between releases without a format bump, because no artifact
on the wire depends on it.
