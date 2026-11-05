# Security audit and hackathon compliance — 2026-11-05

Scope: `MCPVOT/hermes-zkagent` at `perf/hook` (includes the `perf/hook` and `ci` commits) and
`MCPVOT/zkagent` at `perf/prover`. **Nothing has been published.** Both repositories are
private, no catalog PR is open, no Typeform entry, no X post.

## Method

Every finding below came from a command run on this machine, not from reading the README:

```bash
stat -c '%a %U %n' plugin-data/zkagent-attest/{authority.hex,keys,keys/pk.bin}
grep -nE "shell=True|os\.system|eval\(|exec\(|pickle|requests|urllib|socket\." __init__.py
grep -n "subprocess.run" -A4 __init__.py
grep -nE "run_id|index|Path\(" __init__.py
grep -nE "unsafe|unwrap\(|expect\(" crates/zkagent-proofs/src/keyfile.rs
grep -n "Validate::|Compress::" crates/zkagent-proofs/src/keyfile.rs
python3 scripts/check.py                      # secrets, binaries, manifest, smoke, hermes gates
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps -p zkagent-proofs
cargo test --all-features                     # 149 passed
```

## Findings

| # | Finding | Severity | Evidence | Status |
|---|---|---|---|---|
| 1 | No secret material is committed or shipped | — | `scripts/check.py` secret scan over the tree: PASS; `git log` authors all `@users.noreply.github.com` | verified |
| 2 | No bundled executables | — | detector over file magic (ELF/MZ/Mach-O/wasm): PASS; the plugin resolves `zkagent` from `PATH`/`ZKAGENT_BIN` | verified |
| 3 | No shell interpolation of tool arguments | — | `subprocess.run([binary, *args], capture_output=True, text=True, timeout=…)` — argv list, no `shell=True`, no `os.system`, no `eval`/`exec`/`pickle` | verified |
| 4 | No network access | — | no `requests`, `urllib`, `socket`, `http` in the plugin; the protocol binary makes none | verified |
| 5 | Path traversal via tool arguments | — | `execution_id` is coerced with `int()` before it reaches `runs/run-<id>`; `index` is bounds-checked against the receipt's action count; `Path(run["dir"])` comes from our own state file | verified |
| 6 | Key material permissions | low | `authority.hex` 0600, `keys/` 0700; `keys/pk.bin` is 0644 but lives inside the 0700 directory (the proving key is not a secret; the toxic waste was discarded at setup) | accepted, documented |
| 7 | Proving-key reader (new in `perf/prover`) weakens validation | — | `Compress::Yes, Validate::Yes` per point, length bounds-checked before allocation, no `unsafe`, `expect` only in tests, truncated input is an error | verified |
| 8 | Circuit or proof format changed by the perf work | — | 7442 constraints, 17 instance variables, 16 public inputs, proof 2326 B, `FORMAT_VERSION` 3, proof file byte-identical old vs new | verified |
| 9 | New dependency (`rayon`) | low | `rayon = "1"`, MIT/Apache-2.0, build-time only from crates.io; no network at runtime; threaded decompression keeps index order | accepted |
| 10 | Hook can lose a row if the append fails | low | `_record` swallows exceptions so instrumentation never breaks a tool call | accepted, documented |
| 11 | Ledger concurrency | — | session header claimed with `O_EXCL`; 2 processes x 50 calls produced 100/100 rows over 3 trials | verified |
| 12 | Low-entropy arguments could be brute-forced from a disclosed digest | medium | disclosure reveals `sha256(canonical args)` truncated to 63 bits | accepted and stated in SECURITY.md: do not disclose a slot whose arguments are guessable |
| 13 | The agent writes its own ledger | medium | by design; a hostile agent can omit a call (a smaller, still-true statement) but cannot make the circuit accept a total it did not spend | accepted, stated in SECURITY.md |
| 14 | Single-party trusted setup | medium | `setup` discards the seed; development-grade, not a ceremony | accepted, stated |
| 15 | CI caught a rustdoc `private_intra_doc_links` error on ubuntu | — | fixed in `f8d2eb6`; verified locally with `RUSTDOCFLAGS=-D warnings` | fixed |

No `unsafe`, no dynamic code loading, no credential store read, no write outside
`plugin-data/zkagent-attest/`, no rebinding of Hermes internals (`plugins validate` → "no core
override" passes).

## Hackathon rule compliance (catalog-submission §Admission rules)

| Rule | Status | Evidence |
|---|---|---|
| 1 human-merged gate | ok | entry will be a PR to `NousResearch/hermes-agent`, no self-serve path used |
| 2 exact SHA pin | ok | plugin repo is a git repo with real commits; the pin will be the merged SHA |
| 3 no self-updating code | ok | no updater, no release downloader in the plugin (the checksum fetch planned for setup is a documented fetch, not a self-replacer) |
| 4 SHA bumps are new PRs | ok | no automation will bump anything |
| 5 owner submission | ok | `MCPVOT` owns both repositories |
| 6 capabilities match reality | ok | `plugins validate` → "declared tools/hooks matches registrations" |
| 7 install scanner | ok | `security scan — safe` |
| 8 desktop surface | n/a | no `desktop/plugin.js` |
| 9 no core override | ok | "no runtime rebinds of Hermes core" |
| 10 dependency policy | ok | stdlib only; the Rust crate is a separate repo and not a plugin dependency |
| 11 credentials via Hermes | ok | no credential read at all; no `requires_env` |
| 12 approvals respected | ok | no approval routing, no child processes besides `zkagent`, no unattended prompts |
| 13 disclose risky behaviour | **pending** | README discloses the subprocess, the data dir and the threat model; the catalog PR description must restate it |
| 14 truthful compatibility | ok | `requires_hermes: ">=0.22.5"`, SemVer, not newer than the installed release |
| 15 no skins/forks of bundled plugins | ok | independent plugin |
| 16 one listing per lineage | ok | first listing of this plugin |
| 17 no unscannable payloads | ok | no binaries; magic-byte scan passes; the planned binary comes from the upstream release, checksum-pinned and disclosed |
| 18 password managers | n/a | no login backend |

## What must be true before the visibility flip

- [x] secrets and key-material scan over the whole tree, not just HEAD
- [x] no bundled executables
- [x] capabilities match `register()`
- [x] the tamper case rejects (`public_input_mismatch`, exit 1)
- [x] proof artifacts byte-identical after the perf change
- [x] CI green on the exact commit to be pinned (re-running after `f8d2eb6`)
- [ ] PR #20 merged (protocol infra redaction)
- [ ] PR #21 merged (perf) and #1 merged (plugin)
- [ ] repository description no longer says "private development"
- [ ] private vulnerability reporting enabled on both repositories
- [ ] release `v0.3.0` with checksum-pinned assets, so a fresh install needs no manual binary
- [ ] catalog entry PR description restates rule 13 disclosures

## Verdict

The code is fit to publish; the remaining items are administrative plus one product gap (the
binary fetch). Nothing here is public yet, and nothing will be until the checklist above is
complete and the owner says the word.
