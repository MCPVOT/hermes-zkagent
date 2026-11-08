# Catalog submission — `zkagent-attest`

## What it is

A Hermes plugin that gives a session a receipt a third party can check.

Every tool call is recorded in a ledger with its cost and a 64-bit digest of its
arguments. Four tools and one hook let the agent, or the person running it, turn
that ledger into a policy-bound proof:

| Tool | What it does |
| --- | --- |
| `zkagent_status` | pending action count, runs, last attestation |
| `zkagent_attest` | signs a policy (spend cap, action cap, expiry) and proves the session stayed inside it |
| `zkagent_verify` | verifies a receipt, or the whole session with `session: true` |
| `zkagent_disclose` | reveals exactly one action's digest plus its inclusion path |

Hook: `post_tool_call` records target id, cost and a digest of the canonical
arguments. Arguments are hashed, never stored.

The proof is Groth16 over BN254, 7,442 constraints, format 3. A verifier learns
the totals, the action count and that the session stayed under the cap. It does
not learn what the agent did. One action can be revealed afterwards, with a path
that proves it belongs to the attested set.

## Why it needs to be a plugin and not a skill

The value is the verifier, not the prompt. A skill can tell an agent to keep a
log; it cannot produce an artifact that a third party checks without trusting
the agent. This plugin owns four tools, keeps state across a session, and drives
a proving binary. That is what the catalog is for.

## Install

```bash
hermes plugins install zkagent-attest
```

The plugin does not bundle a binary. On first use it runs the pinned installer,
which downloads `zkagent` **v0.4.0+2026-11-08** from
`MCPVOT/zkagent` releases, checks it against a sha256 compiled into the
installer, and installs it to the Hermes cache. `ZKAGENT_BIN` overrides the
path if you already have the binary.

Platforms: Linux x86_64, macOS aarch64, Windows x86_64.

## What the plugin touches

- **No network.** The plugin itself makes no requests. The installer does, once,
  to fetch the pinned binary.
- **No credential reads.** It does not open `.env`, `auth.json`, keyrings or any
  file outside its own data directory.
- **Writes** are confined to `plugin-data/zkagent-attest/`: ledger, pending
  actions, receipts, keys, run artifacts.
- **One subprocess**, `zkagent`, invoked with an argument vector and no shell.
- **No bundled executables.** The catalog artifact is Python source only.
- **No core override.** Nothing in Hermes is patched or monkey-patched.

## Evidence

- Security audit with 15 findings and their evidence:
  `docs/SECURITY-AUDIT.md` in `MCPVOT/hermes-zkagent`.
- Gates: `python3 scripts/check.py` and `hermes plugins validate . --install-deps`.
- Smoke test drives the real binary: five recorded calls, attest accept, verify
  accept, a one-byte tamper rejected with `public_input_mismatch`, replay
  refused, session aggregate verified.
- Upstream library: `MCPVOT/zkagent`, MIT, 156 tests passing on master.

## Disclosures and accepted risks

Restated here in full, because a reviewer should not have to open the repo to
learn them:

1. **Single-party trusted setup.** The proving and verifying keys come from a
   setup run by this project. There was no multi-party ceremony. A malicious
   key generator could forge proofs. This is development-grade and is documented
   as such.
2. **The agent writes its own ledger.** The proof is arithmetic over the actions
   the hook recorded. It does not prove the agent recorded everything, or that
   the recording is honest. An agent that skips the hook produces a smaller
   honest receipt. What the proof buys is privacy and non-tampering after the
   fact, not omniscience.
3. **Disclosed digests can be brute-forced.** A disclosure reveals a digest of
   arguments, not the arguments. For low-entropy arguments, a guess can be
   confirmed against the digest. Disclose only what you are willing to have
   guessed.
4. **Replay protection is local.** It refuses a receipt the same installation has
   already accepted, recorded in `seen.json`. It is not a global nullifier and
   does not stop a second machine accepting the same receipt.
5. **Eight actions per receipt.** A receipt covers at most eight actions; longer
   sessions are attested in batches. Verification of the aggregate session index
   binds the receipts to a total, so a missing batch is detectable.
6. **The spend figures are what the hook assigned.** Cost per tool is a table in
   the plugin settings, visible and editable. The cap is enforced against those
   numbers, which are a policy input, not a measurement of real spend.
7. **Verification is local too.** `zkagent_verify` checks the artifact against
   the keys on disk. A verifier who does not trust those keys should compare the
   session root out of band.

## Demo

49 seconds, recorded on the pinned release, no audio needed: banner, five
recorded calls, `accept · actions 5 · total 9 · cap 20`, verify accept, one byte
changed in the proof -> `REJECT` with exit 1 and `public_input_mismatch`, then
one action disclosed while four stay hidden.

Screenshots: `01-banner.png`, `02-accept.png`, `03-reject.png`, `04-disclose.png`.

## Checklist

- [x] Plugin manifest validates (`hermes plugins validate . --install-deps`)
- [x] No bundled executables
- [x] No core override
- [x] No secret reads, no network from the plugin
- [x] MIT licensed
- [x] Security audit published
- [x] Disclosures restated in this description
- [ ] Demo video link (posted to X, tagged @NousResearch)
