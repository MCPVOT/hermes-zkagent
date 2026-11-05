# Agent instructions

This repository is governed by [CONSTITUTION.md](CONSTITUTION.md). Read it first; it
supersedes this file.

You are working on a Hermes plugin, not on the protocol. The protocol is
`MCPVOT/zkagent` (Rust, Groth16); this repository is the thin, reviewable Python surface that
records a session and drives that binary.

## What this plugin is

One hook and four tools:

```
post_tool_call  -> ledger.py behaviour inside __init__.py   (records one action per call)
zkagent_status  -> binary, pending count, last run
zkagent_attest  -> policy + receipt + proof + verify         (the only writer of a run)
zkagent_verify  -> re-verify a run
zkagent_disclose-> one digest + its inclusion path
```

Single-file layout on purpose: Hermes loads `__init__.py` as a module, so relative imports
are not available. Do not split it into a package unless you have proven the loader imports
it as one.

## The gates, in order, before any push

```bash
python3 tests/smoke.py                      # needs ZKAGENT_BIN or zkagent on PATH
hermes plugins validate . --install-deps    # the same checks catalog CI runs
hermes plugins doctor .                     # discovery + register() in a temp HERMES_HOME
```

`smoke.py` asserts behaviour, not existence: five recorded actions, an accepted attestation,
an accepted re-verify, a one-character tamper rejected with `public_input_mismatch`, and a
single-slot disclosure. A green `validate` without a green smoke run proves only that the
module imports.

## Rules for changes

- **Manifest and code change together.** `provides_tools` / `provides_hooks` in
  `plugin.yaml` must match what `register()` does at the same commit or `validate` fails.
- **No new dependency without a reason a reviewer accepts.** The plugin is stdlib-only;
  that is a feature.
- **Never weaken the smoke test to make a change pass.** If a legitimate change breaks it,
  the test is describing behaviour you are removing; say so in the commit and change both.
- **Version and README travel with behaviour.** `plugin.yaml` `version` and the README's
  capability table move in the same commit, because the marketplace page renders the README
  at the pinned SHA.
- **A new tool is a new capability.** Add it to `provides_tools`, give it a schema with a
  real description, and add it to the smoke run.

## Commit shape

Small commits with a body that says what was measured. `git log --oneline` should read like
a change list, and each message should still be true a month later without the diff.

## What not to touch

- `tests/smoke.py` assertions about the verdict, the tamper rejection and the disclosure.
- The zero root placeholder in the receipt template: `zkagent prove` rewrites it, and a
  pre-filled root makes the receipt disagree with the proof.
- The 8-slot batching rule. A bigger circuit is a protocol change, not a plugin change.
