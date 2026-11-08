# Check a receipt yourself

You do not have to believe the plugin, the agent, or me. This directory contains a real
attestation: a signed policy, a receipt, a Groth16 proof, the public verifying key, and a copy of
the proof with one hex character changed.

```bash
cd examples/verify-it-yourself
./verify-it-yourself.sh
```

The script downloads the pinned `zkagent` release, checks it against the published `SHA256SUMS`,
and then runs two verifications:

```
== checksum ok: c678fa1a1b8462d1954c8797fb235630ccbc90fa2300720606b52b895eda4e81

== 1. the real proof
{"execution_id":1001,"format":3,"reason":null,"reason_code":null,"verdict":"accept"}
   exit 0

== 2. one hex character changed in the proof
{"execution_id":1001,"format":3,"reason":"public inputs do not match the supplied policy/receipt",
 "reason_code":"public_input_mismatch","verdict":"reject"}
   exit 1

PASS: the receipt verifies, and the tampered copy does not.
```

## What it proves

- the proof is a valid Groth16 proof for this policy, this receipt and this verifying key
- the receipt's totals and action count are the ones the proof is about: they are public inputs,
  so changing them breaks verification the same way the tampered proof does
- the verifying key is the one in this directory, and it verifies this proof

## What it does not prove, stated plainly

- **not completeness.** The ledger is written by the agent. A receipt proves the actions it lists
  stayed inside the cap; it cannot prove the agent listed everything it did.
- **not the identity of the signer.** The policy is signed by the authority key generated on the
  machine that produced it. A single-party trusted setup means a malicious key generator could
  forge proofs; there was no ceremony.
- **nothing about the actions.** They stay hidden. All you learn is the totals, the count, and that
  they match.

## Requirements

`curl` and `shasum` (macOS) or `sha256sum` (Linux). Linux x86_64 and macOS arm64 assets are
published; the script refuses other platforms rather than pretending.

## Verify a receipt of your own

```bash
zkagent --json verify --keys <dir with vk.bin> \
  --policy policy.json --receipt receipt.json --proof proof.json
```

Exit codes: `0` accept, `1` reject, `2` could not verify. Branch on those, not on the English.
