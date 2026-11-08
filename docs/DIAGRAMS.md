# Diagrams

Three views of the same system: what happens during a session, what a verdict can be, and which
program owns which file. If a diagram and the code disagree, the code is right and the diagram is a
bug — open an issue.

## 1. One attestation, end to end

```mermaid
sequenceDiagram
    autonumber
    participant A as Hermes agent
    participant H as plugin hook
    participant L as ledger (plugin-data)
    participant P as zkagent-attest tools
    participant Z as zkagent binary
    participant V as verifier

    A->>H: tool call (read_file, terminal, web_search …)
    H->>L: append {target, tool, cost, sha256(args)}
    Note over H,L: arguments are hashed, never stored

    A->>P: zkagent_status
    P->>L: count pending actions
    P-->>A: pending_actions, runs

    A->>P: zkagent_attest {max_amount}
    P->>Z: policy --max-amount --expires-at --authority-key
    Z-->>P: signed policy
    P->>Z: prove (actions, policy)
    Z-->>P: proof (Groth16, 7,442 constraints)
    P->>Z: verify --now
    Z-->>P: verdict
    alt accept
        P->>L: consume rows, write run + session index
        P-->>A: accept · actions · total · cap · execution_id
    else reject
        P-->>A: reject · nothing consumed, ledger intact
    end

    V->>Z: verify --keys vk.bin --policy --receipt --proof
    Z-->>V: {verdict, reason_code} · exit 0/1/2
```

## 2. What a verdict can be

```mermaid
stateDiagram-v2
    [*] --> NoVerdict
    NoVerdict --> Accept: proof verifies, session inside cap
    NoVerdict --> Reject: public_input_mismatch / cap_exceeded
    NoVerdict --> CouldNotVerify: unreadable artifact, wrong keys
    Accept --> Replay: same execution presented again
    Reject --> Replay: same evidence, second road
    Accept --> Stale: older than staleAfterSec
    Replay --> [*]
    Stale --> [*]

    note right of CouldNotVerify
        exit 2, not 1: a broken verifier
        must not read as a rejection
    end note
    note right of Replay
        one ledger namespace: a receipt
        and its session cannot both count
    end note
```

## 3. Who owns which file

```mermaid
flowchart LR
    subgraph Hermes
        AG[agent turn] --> HOOK[post_tool_call hook]
        TOOLS[4 tools] --> SVC[plugin __init__.py]
    end
    HOOK --> LEDGER[(state.json + pending.jsonl)]
    SVC --> LEDGER
    SVC --> SEEN[(seen.json · replay guard)]
    SVC --> RUNS[(runs/run-N/{policy,receipt,proof}.json)]
    SVC -->|subprocess, no shell| BIN[zkagent binary]
    BIN --> REL[[release v0.4.1+2026-11-08<br/>sha256-pinned]]
    RUNS --> VERDICT[(last-verdict.json)]
    VERDICT --> WIDGET[Omarchy bar widget<br/>mcpvot.attest · ZK mark]
    SVC -.->|never| NET[no network]
    SVC -.->|never| SECRETS[no .env, no keys]
```

## Reading order

1. this file, for the shape
2. `docs/ARCHITECTURE.md`, for the decisions
3. `CONSTITUTION.md`, for the rules that outrank convenience
