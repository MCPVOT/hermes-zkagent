# Changelog

Format: `semver`, newest first. The plugin version is a plugin concern; the `zkagent` binary it
drives is versioned separately with `semver + date` in
[MCPVOT/zkagent](https://github.com/MCPVOT/zkagent/blob/master/VERSIONING.md).

## 0.5.5

### Added

- `examples/verify-it-yourself/`: a real policy, receipt, proof, public `vk.bin`, and a tampered
  copy, plus a script that downloads the pinned release, checks its sha256, and shows accept then
  reject. A reviewer no longer has to trust the plugin to check a receipt.

### Changed

- the demo scripts read the plugin version from `plugin.yaml` instead of printing a hardcoded one.

## 0.5.4

### Fixed

- **a deleted replay ledger was invisible.** `seen.json` could be removed and every acceptance
  forgotten. `state.json` now carries a guard header (accepted count, high-water id, digest) and
  `verify` refuses a ledger that shrank or was edited, with `reason: replay_guard_lost`.
- **an unreadable `state.json` was read as an empty session**, which silently started a new
  execution-id range and dropped the guard. Now `reason: state_unreadable`. An absent file is still
  a fresh profile.

## 0.5.3

### Fixed

- **widening the cap after a refusal was accepted.** A batch refused at a cap could be attested
  again with a larger one, so the cap was advisory. Refusals are recorded per batch, a wider cap is
  refused with `reason: cap_widened_after_refusal`, and a deliberate widening requires
  `allow_cap_increase: true` and is recorded on the run as `cap_relaxed`.

## 0.5.2

### Fixed

- **the same evidence could be accepted in both modes.** A receipt accepted on its own did not
  block the aggregate containing it, and the reverse order was open too. One ledger namespace now
  answers both: `execution_already_accepted` and `already_accepted_in_session`.

### Changed

- the installer pins `zkagent v0.4.0+2026-11-08`, the release whose prover samples blinding through
  `OsRng` per proof instead of a fixed seed.

## 0.5.1

### Added

- selective disclosure: `zkagent_disclose` reveals one action with its Merkle path.
- session aggregate verification (`session: true`) cross-checks the index against the receipts.

## 0.5.0

### Added

- first release: four tools (`status`, `attest`, `verify`, `disclose`) and a `post_tool_call` hook.
- ledger with a per-call cost table, a signed policy cap, and receipts that publish totals only.
