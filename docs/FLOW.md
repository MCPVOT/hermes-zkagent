# Flow: from a tool call to a verifiable receipt

The whole product in one diagram. Every arrow is something that actually runs; nothing here
is aspirational.

```mermaid
flowchart TD
    A["Agent calls a tool<br/>read_file, terminal, web_search, patch …"] --> B["post_tool_call hook"]
    B --> C["digest = sha256(canonical args)[0..8]<br/>cost from settings<br/>target id from the tool map"]
    C --> D[("pending.jsonl<br/>one JSON row per call")]
    D --> E{"zkagent_attest<br/>max_amount"}
    E -->|"total > cap"| X1["REFUSE<br/>a proof of a false statement<br/>is worse than no proof"]
    E -->|"batch ≤ 8 actions"| F["policy: caps, expiry, chain, allow-list<br/>signed with authority.hex"]
    F --> G["receipt: totals + execution_id<br/>trace_root = zeros"]
    G --> H["zkagent prove<br/>Groth16 over the private actions<br/>fills the real trace_root"]
    H --> I{"zkagent verify"}
    I -->|accept| J[("runs/run-id/<br/>policy, receipt, proof")]
    I -->|reject| X2["verdict reject<br/>reason_code preserved"]
    J --> K["rebuild session.json<br/>Merkle root over receipt roots"]
    K --> L{"session cross-check<br/>roots · totals · counts"}
    L -->|mismatch| X3["session_mismatch<br/>hard failure, nothing recorded"]
    L -->|match| M[("seen.json<br/>accepted execution ids")]
    M --> N["zkagent_disclose index i<br/>one digest + its path"]
    N --> O["zkagent_verify session:true<br/>refuses a replayed id"]
    O --> P["Desktop pane<br/>verdict · totals · disclosure picker"]
```

## The three refusals are the product

| Refusal | When | Why it matters |
|---|---|---|
| `max_amount` exceeded | the session spent more than the cap you asked to prove | a proof of a false statement is worse than no proof |
| `session_mismatch` | the index disagrees with the receipt files | an aggregate that cannot lie about its members |
| `replay` | an execution id was already accepted | a proof is not a subscription; once is the contract |

## Where the secrets are

`authority.hex` (0600) signs policies, `keys/` (0700) holds the circuit keys, and the actions
never leave `actions.json` inside the run directory. A disclosure reveals one 64-bit digest
and three sibling hashes. The arguments themselves are never written anywhere.

## What each stage costs, measured on this laptop

```
hook            0.051 ms per tool call        (append, no read-modify-write)
attest          0.6 s once per batch          (prove 0.44-0.63 s + verify 11 ms)
verify          11 ms
disclose        1 ms + one verify             (zero added constraints)
session check   sha256 over ≤ N roots, microseconds
```
