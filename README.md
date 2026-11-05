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
| `zkagent_status` | Which zkagent binary is in use, how many actions are pending, the last attestation |
| `zkagent_attest` | Sign a policy cap, prove the session inside it, return the verdict |
| `zkagent_verify` | Re-verify an attestation (`accept` / `reject`, machine-readable) |
| `zkagent_disclose` | Reveal one action's digest plus its inclusion path |

Plus a `post_tool_call` hook: it records every tool call automatically, so the ledger fills
while the agent works and `zkagent_attest` is a single call at the end.

Plus a **Desktop pane** (`desktop/plugin.js`): the last attestation, read-only.

## The Desktop pane

Hermes Desktop loads `desktop/plugin.js` as the desktop half of this package — no build step,
no Tauri app, one plain ESM file. It registers a **zkagent receipt** pane you dock anywhere:

| What it shows | Where it comes from |
|---|---|
| verdict (ACCEPT / REJECT) with the run's reason | `state.json` run header |
| live re-check: verdict + machine-readable `reason_code` | `zkagent verify --json`, run on display |
| actions (`n of 8 slots`), total against the cap, execution id, completion time | `runs/run-<id>/receipt.json` |
| the trace root, truncated, with a copy action | the receipt's `trace_root` |
| one slot's disclosed digest and whether its inclusion path verifies | `runs/run-<id>/disclosure-<i>.json`, checked by the binary |

Two things worth knowing:

- **It writes nothing.** No route calls `attest` or `disclose`, creates no directory, and never
  edits the ledger. A slot without a `disclosure-<i>.json` says so and names the
  `zkagent_disclose` call that would create one, instead of disclosing on your behalf.
- **The verdict and the re-check are different claims.** The badge is what the attestation
  decided; the "re-checked now" line is `zkagent verify` re-run against the stored artifacts,
  which is also the only place a `reason_code` exists. An old receipt whose policy has since
  expired re-checks as REJECT even though it was accepted when it was made — both are shown.

Why a small Python backend: the pane reads through `ctx.rest` into
`dashboard/plugin_api.py` (mounted at `/api/plugins/zkagent-attest/`), because the SDK's file
door (`ctx.files`) is scoped to the folder the app owns for the plugin and refuses Hermes'
own data — `plugin-data/` is unreachable from the renderer by design. The backend runs in the
gateway beside the tools that wrote the files, and answers only `GET /latest` and
`GET /disclosure?index=N`.

Enable it in **Capabilities → Plugins** (a package's desktop half ships opt-in), and make
sure the plugin is in `plugins.enabled` in `config.yaml` — the Python backend is imported
under that gate, and `ctx.rest` returns 404 without it.

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
zkagent_verify {}
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

Stored under `plugin-data/zkagent-attest/state.json` as `costs`. The caps in
`zkagent_attest` are in the same units: `max_amount` is the session total, `max_per_action`
the most expensive single call.

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
- writes under `plugin-data/zkagent-attest/` only: keys, policies, receipts, proofs, state
- a `post_tool_call` hook that appends one JSON row per tool call and never raises
- no network calls, no credential reads, no auto-updater, no shell outside `zkagent`
- with the Desktop half loaded: two read-only routes on the gateway's plugin namespace
  (`GET /api/plugins/zkagent-attest/latest`, `GET …/disclosure?index=N`). They write nothing;
  the only process they start is `zkagent verify` (no `--seen` file), once per pane load or
  slot selection, so showing a receipt costs one proof verification

## Limitations, stated

- One trusted setup per install, single-party, development-grade. Not a ceremony.
- 8 actions per receipt, by circuit design.
- The ledger is per profile and survives updates; clear `state.json` to start a fresh one.
- `chain_id` is carried through the receipt, not enforced against a network.

## Licence

MIT. zkagent is MIT, by MCPVOT.
