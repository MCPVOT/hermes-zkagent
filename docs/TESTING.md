# Testing

Three gates. Each answers a different question, and passing one is not evidence about the
others.

## 1. Does it load?

```bash
hermes plugins validate . --install-deps
hermes plugins doctor .
```

`validate` is the catalog's own check: the manifest parses, `requires_hermes` parses,
`register()` runs in isolation, the declared `provides_tools` / `provides_hooks` match what
was registered, no tool name collides with a builtin, the security scan is clean, and no
core module is rebound. `doctor` repeats discovery and registration in a temporary
`HERMES_HOME`.

Neither runs a tool. A plugin can pass both and return nothing useful.

## 2. Does it behave?

```bash
ZKAGENT_BIN=~/work/zkagent/target/release/zkagent python3 tests/smoke.py
```

What it asserts, in order:

```
status   binary resolved, version/format/commit printed
hook     5 tool calls recorded -> pending == 5
attest   verdict accept, 5 actions, total 9, cap 20
verify   verdict accept
tamper   one hex character flipped in proof.json -> exit 1, public_input_mismatch
disclose slot 2 -> a disclosure file that exists and contains a digest
```

The tamper case is the one that matters. An attestation that says `accept` proves the happy
path ran; a tampered proof that still says `accept` proves the plugin is lying. The smoke
test fails loudly in that case.

## 3. Does it work where a user will run it?

```bash
hermes plugins install MCPVOT/hermes-zkagent --ref <40-hex sha> --no-enable
hermes plugins enable zkagent-attest
hermes plugins doctor zkagent-attest
```

Then, in a **new** session (tools register at session start; hooks can go live on a gateway
reload):

```
zkagent_status
zkagent_attest {"max_amount": 20}
```

This is the only step that proves the model can actually see and call the tools.

## What the tests do not cover

- A forged ledger produced by a hostile agent (out of scope; see `SECURITY.md`).
- Windows and macOS. The code has no platform branch, and `plugin_data_dir()` resolves the
  host home, but the smoke run has only been executed on Linux.
- Long sessions: batching beyond 8 actions is exercised by the refusal path, not by a
  50-call session.

## Reproducing a release

1. `tests/smoke.py` green against the binary version you intend to require.
2. `validate` and `doctor` green on the exact commit.
3. Bump `version` in `plugin.yaml` and the README capability table together.
4. Commit, push, pin the new 40-character SHA in the catalog entry.
