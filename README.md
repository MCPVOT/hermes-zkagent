# zkagent-attest

Verifiable receipts for what a Hermes agent actually did.

Your agent's transcript is a claim by the agent. This plugin makes it a **proof**: every
tool call is recorded, the session is proved to have stayed inside a policy cap you signed,
and the receipt publishes totals only. The actions stay private. When you need to show one
of them, you reveal exactly one, with the Merkle path that places it in the attested set.

**Read next:** [Constitution](CONSTITUTION.md) (the eight non-negotiables) ·
[Architecture](docs/ARCHITECTURE.md) (data flow, files, failure modes) ·
[Security](SECURITY.md) (threat model, what a receipt is worth) ·
[Testing](docs/TESTING.md) (the three gates) ·
[How it gets used](docs/WORKFLOW.md) ·
[Agents](AGENTS.md) (instructions for an agent working on this repo).

## Install

```bash
python3 scripts/install-zkagent.py               # pinned v0.3.1+2026-11-05, sha256 verified
hermes plugins install MCPVOT/hermes-zkagent     # then: hermes plugins enable zkagent-attest
python3 scripts/check.py                         # secrets, binaries, manifest, smoke, hermes gates
```

```
agent works ──▶ tool calls recorded ──▶ policy signed ──▶ Groth16 proof ──▶ ACCEPT / REJECT
                                          (caps)                            (254-byte receipt)
```

## What you get

| Tool | What it does |
|---|---|
| `zkagent_status` | Which zkagent binary is in use, how many actions are pending, the last attestation, the session index |
| `zkagent_attest` | Sign a policy cap, prove the session inside it, add the receipt to `session.json` |
| `zkagent_verify` | Verify one receipt, or the whole session with `{"session": true}`; refuse a replay |
| `zkagent_disclose` | Reveal one action's digest plus its inclusion path |

Plus a `post_tool_call` hook: it records every tool call automatically, so the ledger fills
while the agent works and `zkagent_attest` is a single call at the end.

## Sessions longer than one receipt

The circuit has 8 action slots, so a real session produces several receipts. Each one stands
on its own; `session.json`, written beside `runs/` after every attestation, is the index that
ties them together:

```json
{
  "total": 17, "receipt_count": 2, "action_count": 9,
  "root": "59fbac56…",
  "receipts": [
    {"execution_id": 1001, "trace_root": "b33e6278…", "total": 9, "actions": 5, "receipt": "runs/run-1001/receipt.json"},
    {"execution_id": 1002, "trace_root": "caec5dd7…", "total": 8, "actions": 4, "receipt": "runs/run-1002/receipt.json"}
  ]
}
```

`root` is a sha256 binary tree over the ordered receipt roots: leaf =
`sha256(trace_root bytes)`, node = `sha256(left ‖ right)`, an odd node is promoted unchanged,
and an empty session is 64 zeros — small enough to re-implement from that sentence. It
carries no signature, and needs none: every field is either copied from a receipt or
recomputed from the receipts, so `zkagent_verify` re-checks the listed roots against the
receipt files on disk, each per-receipt total against its `total_amount`, and their sum
against the session total. **Any disagreement is a hard failure**, not a warning: a
`session_mismatch` verdict means the index and the evidence do not describe the same session,
and nothing is accepted until the index is rebuilt.

```
zkagent_verify {"session": true}    # every receipt accepts, roots match, totals add up, one root
```

## Replay

`zkagent_verify` records what it accepted in `seen.json`: the execution ids it verified one
by one, and the session roots it accepted whole. A second attempt at the same id (or the same
root) is refused — `verdict: "replay"`, `reason: "already_accepted"`, and a message naming
when it was first accepted. A receipt is evidence for one execution, once; presenting it
again would make one event look like two. Execution ids are never reused: a session started
over a cleared `state.json` continues above the highest accepted id.

Stated plainly: this is a **local** guard. It stops an agent from re-verifying its own
evidence and reporting a second acceptance, which is the failure the protocol's
`Receipt::is_replay` names. It cannot stop someone who copies the files and verifies them
elsewhere; that needs the chain to track spent receipts, which is the protocol's job.

## Why this is a plugin and not a skill

A skill can tell the model to write a receipt. It cannot fire on every tool call, and it
cannot make the receipt verifiable by a third party. The hook is the part that cannot be
faked, and the proof is the part that cannot be forged.

## Requirements

- **zkagent**, the CLI (Rust). Install it from the upstream releases at
  `https://github.com/MCPVOT/zkagent/releases` and make sure `zkagent` is on `PATH`, or
  point `ZKAGENT_BIN` at the binary. This plugin does not bundle a binary: the catalog
  forbids shipping compiled payloads, and a checksum-pinned download from the upstream
  release is the honest alternative.
- No Python dependencies. Standard library only.
- No API keys. Nothing here talks to the network.

## Usage

```
zkagent_status {}
zkagent_attest {"max_amount": 20}
zkagent_verify {}                    # one receipt
zkagent_verify {"session": true}     # every receipt in session.json, plus the Merkle root
zkagent_disclose {"index": 2}
```

`zkagent_attest` attests the oldest pending batch (zkagent's circuit has 8 action slots per
receipt). Anything beyond 8 stays pending for the next attestation.

## The cost table

Every tool call costs a number of policy units. The default is deliberately small and
visible, and it is editable without reinstalling:

```json
{"read_file": 1, "write_file": 2, "patch": 2, "terminal": 3, "web_search": 2, "web_extract": 3, "browser_exec": 3}
```

The numbers are declared in `plugin.yaml` as `config_schema`, so the host renders them as
settings under `plugins.entries.zkagent-attest.settings` (the Desktop Plugins tab writes the
same place), and the defaults there are exactly `DEFAULT_COSTS` in `__init__.py`. Read
precedence, stated because it is observable: host settings > `state.json` `costs` >
`DEFAULT_COSTS`. The plugin reads the host settings through `ctx.get_config` and falls back
to `state.json` when the host exposes no value (an older Hermes, or a hand-run module); on
such a host, editing `state.json` still works. The caps in `zkagent_attest` are in the same
units: `max_amount` is the session total, `max_per_action` the most expensive single call.

## Privacy, precisely

- The **digest** of a call's arguments is recorded; the arguments are not. `zkagent_disclose`
  reveals that digest, not the payload.
- The **receipt** publishes total amount, action count, completion time, chain id, execution
  id and a trace root. It does not name the tools that ran.
- The **proof** is zero-knowledge over the actions: the verifier learns only that the
  published totals are explained by some action set inside the signed policy.

What this does **not** prove: that a tool's result was honest, that the agent ran
unsandboxed-but-honest, or that the recorded set is exhaustive if something bypassed the
hook. It proves the recorded set is consistent with the receipt and inside the policy.

## What it does to your machine

- runs `zkagent` as a subprocess (`setup`, `policy`, `prove`, `verify`, `disclose`)
- writes under `plugin-data/zkagent-attest/` only: keys, policies, receipts, proofs, the
  session index, the replay guard, state
- a `post_tool_call` hook that appends one JSON row per tool call and never raises
- no credential reads, no auto-updater, no shell outside `zkagent`

## Limitations, stated

- One trusted setup per install, single-party, development-grade. Not a ceremony.
- 8 actions per receipt, by circuit design.
- The ledger is per profile and survives updates; clear `state.json` to start a fresh one.
  `seen.json` deliberately survives that, so an execution id accepted once is never reused.
- `session.json` is an index, not a proof: it is only as good as the receipts under `runs/`,
  which is exactly why `zkagent_verify` re-reads them instead of trusting it.
- Replay refusal is local to this profile. It cannot see a receipt presented on another
  machine.
- `chain_id` is carried through the receipt, not enforced against a network.

## Licence

MIT. zkagent is MIT, by MCPVOT.
