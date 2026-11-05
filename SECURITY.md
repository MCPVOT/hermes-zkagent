# Security

## Reporting

Open a private vulnerability report on this repository (Security → Advisories → Report a
vulnerability), or email the maintainer listed in `plugin.yaml`. Please do not open a public
issue for anything that could let a receipt lie.

## What this plugin can touch

| Surface | Access |
|---|---|
| Files | `<profile>/plugin-data/zkagent-attest/` only |
| Processes | `zkagent` (setup, policy, prove, verify, disclose), resolved from `ZKAGENT_BIN` or `PATH` |
| Network | one request, at most, and only when no `zkagent` binary is found: the pinned release URL from `MCPVOT/zkagent`, sha256 verified before install |
| Credentials | none; nothing reads `.env`, `auth.json` or any keychain |
| Host code | public plugin API only (`register_tool`, `register_hook`); no core patching |

## Threat model

**What the receipt is worth.** It proves that the published totals are explained by some set
of actions, inside the signed policy, without revealing them. It does not prove the tool
results were honest, and it cannot.

**The hook can be bypassed.** Anything that runs outside Hermes (a script, a cron job, an
external process) is not in the ledger. A receipt then describes less than the session did.
The plugin cannot detect this; treat the receipt as a statement about recorded actions.

**The agent writes the ledger.** The tool calls being attested are made by the same
process that records them. A compromised or adversarial agent could choose not to call a
tool, which is a *smaller* execution and still a true statement. It could not make the
proof accept a total it did not spend, because the circuit checks it.

**Single-party setup.** `zkagent setup` runs once per install, with a random seed that is
discarded. That is development-grade: an operator who controls setup could in principle
forge proofs. A multi-party ceremony is out of scope and stated as such in the protocol
repository.

**Disclosure reveals a commitment, not a payload.** The digest is a one-way hash of the
arguments. Low-entropy arguments could be brute-forced from a digest; do not disclose a
slot whose arguments are guessable if the arguments themselves are sensitive.

**The session index is not trusted, it is checked.** `session.json` has no signature; it is an
index over receipts. It is accepted only when every listed root, total and action count
matches the receipt file it names, the sums match the session total, and every receipt
verifies. A doctored index therefore fails closed (`session_mismatch`); it cannot change what
a receipt proves, and it cannot add a receipt that does not exist.

**Replay refusal is local.** `seen.json` records the ids and session roots this profile has
accepted, and refuses a second acceptance. It cannot see the same receipt presented on
another machine or another profile, so it is not a global anti-replay mechanism — that needs
the chain to track spent receipts, which the protocol owns. Treat it as a guard against an
agent inflating its own acceptance count, not as a settlement layer.

## Supply chain

- No bundled binaries and no self-updater. One optional download: when no `zkagent` binary is
  found, the plugin runs `scripts/install-zkagent.py`, which fetches the pinned release from
  `MCPVOT/zkagent`, checks its sha256 against the table committed in that script, and refuses
  to write anything on a mismatch. `ZKAGENT_AUTOINSTALL=0` disables it entirely.
- The download is reported, not silent: the tool result carries `install.attempted`,
  `install.ok`, the installer's own output and the destination path.
- No Python dependencies outside the standard library.
- The `zkagent` binary is the trust boundary: pin it (`ZKAGENT_BIN`) and check
  `zkagent version` reports the commit and artifact format you expect.

## Known non-goals

Sandboxing the agent, metering real money, proving that a policy was *good*, and preventing
an agent from doing something it is allowed to do.
