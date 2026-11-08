# The submission, in one place

Everything about the hackathon entry, so nobody has to reconstruct it from chat.

## What was submitted

| item | where |
| --- | --- |
| plugin repo | https://github.com/MCPVOT/hermes-zkagent |
| library it drives | https://github.com/MCPVOT/zkagent |
| catalog entry | https://github.com/NousResearch/hermes-agent/pull/149603 |
| demo video | https://x.com/MCPVOT/status/2119241113760649642 |
| marketplace widget | https://github.com/MCPVOT/omarchy-zkagent |
| entry form | submitted, "Submission Received" (2026-11-08) |

## Where it stands

```
catalog PR   OPEN · MERGEABLE · one file changed (plugin-catalog/zkagent-attest.yaml)
triaged      alt-glitch (Nous Research): comp/plugins, P3, type/docs, tool/mcp
checks       not started; a maintainer starts the catalog validation
reviews      0
merged       no
deadline     2026-11-11 13:00 PST
```

Nothing is blocked on our side: the entry parses, every image resolves at the pinned commit, and the
plugin at that pin passes nine gates.

## What is behind the pin

```
plugin       0.5.8, pinned by sha f962664c
zkagent      0.4.1+2026-11-08, format 3, 7,442 constraints, checksum-pinned release
gates        9/9: secrets, binaries, manifest, docs, artifact shapes, version, guards, smoke, validate
tests        19 guard tests · 24 leaderboard tests · 156 library tests
```

## If a maintainer asks for a change

1. Read the comment on the PR.
2. Change the plugin repo, run `python3 scripts/check.py --binary ~/.local/bin/zkagent`.
3. Bump the version, add a changelog entry, push.
4. Move the pin in the entry: re-read the file at the PR head, replace `sha:` and `version:`, PUT it
   back to the fork branch.
5. Confirm every image URL still resolves at the new sha.

That sequence is in `VERSIONING.md` in the library repo and in the skill
`hermes-plugin-publishing/references/catalog-pin-and-local-sync.md`.

## What is not claimed

- the ledger is agent-authored, so a receipt is not a complete account of what an agent did
- the trusted setup is single-party, development grade
- replay protection is local to one installation
