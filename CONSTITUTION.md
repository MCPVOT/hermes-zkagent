# Constitution

The non-negotiables of `zkagent-attest`. Everything else in this repository, including
`AGENTS.md` and `CONTRIBUTING` conventions, defers to this file. A change that violates a
rule here is not a change to discuss; it is a change to reject.

## 1. The verdict is the product

A tool returns `accept`, `reject` or could-not-verify, and the exit code says the same
thing. No prose may soften a `reject`, no summary may upgrade a could-not-verify, and no
retry may turn a rejection into an acceptance. A plugin that reports "verified" because it
ran is worse than no plugin.

## 2. Never widen a cap to make a proof pass

The cap is the claim. If the session total exceeds the cap the caller asked to prove, the
tool refuses and says so. Raising `max_amount` after seeing the number is forging the
evidence, not configuring it.

## 3. The action set is private by default

Arguments are hashed, never stored. A disclosure reveals one digest and its inclusion path,
never a payload. No log line, error message or state file may contain tool arguments.

## 4. No bundled executables

No ELF, PE, Mach-O or wasm in this repository, ever. The plugin drives `zkagent` from
`PATH` or `ZKAGENT_BIN`. When the download path lands, it is a checksum-pinned fetch from
the upstream release, disclosed in the README and the catalog PR.

## 5. No override of the host

Public plugin surfaces only: `register_tool`, `register_hook`, `register_skill`, and the
plugin's own data directory. No monkey-patching Hermes modules, no rebinding registry
entries, no writing outside `plugin-data/zkagent-attest/`.

## 6. No network, no credentials

The plugin makes no outbound request and reads no secret. The only subprocess is `zkagent`.
If a future feature needs a network or a key, it goes through Hermes' own routes and this
document changes first.

## 7. The hook never breaks a tool call

`post_tool_call` records or does nothing. Any exception inside it is swallowed. Instrumentation
must not be able to fail a user's work.

## 8. A limitation is written down, not fixed in the prose

Hook bypass, single-party setup, eight action slots, no proof of result honesty: each is in
the README and the skill. Deleting a limitation to make the pitch stronger is the one edit
that turns this project into marketing.
