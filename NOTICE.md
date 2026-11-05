# Notices

## This repository

MIT, see [LICENSE](LICENSE). Copyright MCPVOT.

## What it talks to

**zkagent** — the protocol and CLI this plugin drives, MIT, MCPVOT,
<https://github.com/MCPVOT/zkagent>. Not vendored here: the plugin resolves the binary from
`ZKAGENT_BIN` or `PATH` and never ships one.

**Hermes Agent** — the host, MIT, Nous Research, <https://github.com/NousResearch/hermes-agent>.
This plugin uses only the public plugin API: `register_tool`, `register_hook`, and
`plugin_data_dir()`.

## Ideas borrowed, not code

The repository's document shape — a constitution that outranks the other docs, a short
agent-instructions file, a `docs/` folder for design and testing, and a "how this is
actually used" note — is modelled on two public projects that do it well:

- **obs-rust/obs-studio** — <https://github.com/obs-rust/obs-studio> (GPL-2.0). The ranking
  of documents and the habit of stating a non-negotiable before the procedure. No text or
  code copied.
- **MiniMax-AI/mcode-stack** — <https://github.com/MiniMax-AI/mcode-stack> (MIT). The
  per-stack `WORKFLOW.md`, one-command `scripts/check`, and "I use this" issue habit. No
  text or code copied.

Any adapted quotation is attributed where it appears.
