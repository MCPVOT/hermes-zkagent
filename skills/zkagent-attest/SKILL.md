---
name: zkagent-attest
description: Use when a session needs a verifiable receipt: records tool calls, proves the session stayed inside a signed policy cap, and discloses one action on request.
version: 0.1.0
author: MCPVOT
license: MIT
---

# zkagent-attest

A receipt that a third party can check, produced from a session that already happened.

## When to use it

- Someone asks "prove what your agent did" and a transcript is not good enough.
- A spend or action cap must be shown to have held, without publishing every action.
- One action has to be shown in detail while the rest stay private.

## The flow

1. `zkagent_status` — confirm the binary and see how many actions are pending.
2. `zkagent_attest {"max_amount": N}` — sign a cap, prove the session inside it, get a verdict.
3. `zkagent_verify` — re-check the verdict later, or on another machine.
4. `zkagent_disclose {"index": i}` — reveal one action's digest and inclusion path.

## Reading the verdict

- `accept`, exit 0 — the published totals are explained by some action set inside the policy.
- `reject`, exit 1 — with a `reason_code`: `public_input_mismatch`, `invalid_proof`,
  `expired`, `malformed_proof`, `chain_mismatch`, `unauthorised_policy`.
- `could not verify`, exit 2 — the artifact could not be read at all.

Never report "verified" from the tool's prose. Quote the `verdict` field and the exit code.

## Rules that keep it honest

- **Never raise `max_amount` to make a failing attestation pass.** A cap set after the
  fact proves nothing; the number is the claim.
- **8 slots per receipt.** `zkagent_attest` takes the oldest pending batch; the rest stay
  pending. A session longer than 8 calls needs several attestations, not a bigger circuit.
- **The digest is not the payload.** Disclosure reveals a commitment to the arguments, not
  the arguments. Do not describe it as publishing the call.
- **The hook records; it does not judge.** A tool call that bypassed Hermes (an out-of-band
  script) is not in the ledger, and the receipt then describes less than the session did.
- **Pin the binary.** `ZKAGENT_BIN` or `zkagent` on PATH. A build from a feature branch and
  an artifact from another format look identical until verification fails with a serde error.

## What it will not tell you

Whether a tool's result was honest, whether the policy was a good policy, or whether the
agent behaved well. It answers one question: do the published totals come from an action
set that respected the signed caps.
