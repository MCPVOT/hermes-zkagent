# How this gets used

Borrowed from the mcode-stack idea that a repo of agent tooling should say how its author
actually uses it, not only what it contains. This is the real loop, on a real machine.

## The daily case: a session that touches files and runs commands

1. The agent works normally. Nothing is asked of it; `post_tool_call` records each call.
2. Before answering the user, `zkagent_attest {"max_amount": 40}` runs once.

```
attest: accept | actions 8 | total 23 | cap 40
```

3. The receipt and proof land in `plugin-data/zkagent-attest/runs/run-1001/`.

What the user gets: "this session performed 8 actions totalling 23 units, inside a cap of
40, and here is a proof you can verify without seeing them." That is a different sentence
from "trust me".

## The case that made it worth building: a cap that did not hold

A session exceeded the cap the operator intended. The plugin refused to attest against the
smaller number rather than quietly raising it:

```
{"ok": false, "error": "the session total (46) exceeds the cap you asked to prove (40);
 a proof of a false statement is worse than no proof. Raise max_amount or attest fewer actions."}
```

That refusal is the product working. The alternative, a green check on an inflated cap,
would have been a lie with a proof attached.

## The audit case

A third party wants to know one specific action happened. `zkagent_disclose {"index": 2}`
returns that action's digest and the path to the root, and nothing about the other seven.

## What I check every time before trusting it

```bash
python3 scripts/check.py                 # secrets, binaries, manifest/code, smoke, hermes gates
ZKAGENT_BIN=... python3 tests/smoke.py   # the tamper case specifically
```

If the tamper case ever reports `accept`, nothing else in this repository matters until it
is fixed.
