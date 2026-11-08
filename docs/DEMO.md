# The 80-second demo

One take, five beats, no editing. Everything below is runnable on this machine.

## Pre-flight (before recording)

```bash
python3 scripts/install-zkagent.py                 # pins v0.4.0+2026-11-08, verifies sha256
hermes plugins list | grep zkagent-attest          # must say enabled
rm -f "$HERMES_HOME/plugin-data/zkagent-attest/state.json" \
      "$HERMES_HOME/plugin-data/zkagent-attest/pending.jsonl" \
      "$HERMES_HOME/plugin-data/zkagent-attest/session.json" \
      "$HERMES_HOME/plugin-data/zkagent-attest/seen.json"       # empty session, fresh replay guard
hermes -p trilogy-brain                            # NEW session: tools register at start
```

The tools only appear in a session started after the plugin was enabled. If they are missing,
the plugin is enabled but this session predates it.

## Shot list

| t | On screen | Do |
|---|---|---|
| 0:00 | terminal, big font | "This agent claims it stayed under a spend cap. Here is the proof." |
| 0:08 | `zkagent_status` | show binary + `pending_actions: 0` |
| 0:15 | 5 tool calls | `read_file`, `terminal`, `web_search`, `patch`, `read_file` — any real calls |
| 0:30 | `zkagent_status` | `pending_actions: 5` — the hook counted them, nobody asked it to |
| 0:38 | `zkagent_attest {"max_amount": 20}` | verdict **accept**, actions 5, total 9, cap 20 |
| 0:50 | `zkagent_verify {"session": true}` | verdict **accept**, the receipts' roots and totals re-checked against `session.json` |
| 0:56 | `zkagent_verify {"session": true}` again | **replay**, refused — the same session root cannot be evidence twice |
| 0:58 | tamper | `cp proof.json bad.json`, flip one hex char, verify → **reject**, `public_input_mismatch` |
| 1:08 | `zkagent_disclose {"index": 2}` | one digest + its inclusion path; the other four stay hidden |
| 1:15 | close | "Totals are public, actions are not, and a tampered proof fails." |

## The one line that sells it

The tamper beat. An attestation that says `accept` proves the happy path ran; a tampered
proof that still says `accept` proves the tool is lying. Show it failing.

## Tamper, exactly

```bash
R=$(python3 -c "import json,sys;print(json.load(open('$HERMES_HOME/plugin-data/zkagent-attest/state.json'))['runs'][-1]['dir'])")
cp "$R/proof.json" "$R/proof-bad.json"
python3 - "$R/proof-bad.json" <<'PY'
import json,sys,pathlib
p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text())
def flip(n):
    if isinstance(n,dict):
        for k,v in n.items():
            if isinstance(v,str) and len(v)>=16 and all(c in "0123456789abcdef" for c in v.lower()):
                n[k]=("0" if v[0]!="0" else "1")+v[1:]; return True
            if flip(v): return True
    elif isinstance(n,list):
        for i,v in enumerate(n):
            if isinstance(v,str) and len(v)>=16 and all(c in "0123456789abcdef" for c in v.lower()):
                n[i]=("0" if v[0]!="0" else "1")+v[1:]; return True
            if flip(v): return True
    return False
flip(d); p.write_text(json.dumps(d))
PY
zkagent verify --keys "$R/../../keys" --policy "$R/policy.json" \
  --receipt "$R/receipt.json" --proof "$R/proof-bad.json"; echo "exit=$?"   # expect 1
```

## Recording

```bash
omarchy capture screenrecording --fullscreen     # stop with: omarchy capture screenrecording --stop-recording
```

Keep the terminal at ~16 pt, 120 columns, dark theme. Cut nothing; the five beats fit in 80 s
with room to breathe.

## If the session does not show the tools

Run the deterministic fallback on camera instead — it exercises the same four tools against
the real binary and prints the same verdicts:

```bash
ZKAGENT_BIN="$HOME/.local/bin/zkagent" python3 tests/smoke.py
```

## Caption

> Hermes records every tool call. `zkagent` proves the session stayed inside a cap you signed,
> publishes the totals, and keeps the actions private — and a one-byte tamper is rejected.
> Plugin: MCPVOT/hermes-zkagent · protocol: MCPVOT/zkagent
