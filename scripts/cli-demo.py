"""Print the raw JSON of every zkagent tool result, for a screen recording.

The chat UI collapses tool output into one-line chips. This prints the same documents
in full, one screen at a time, so a viewer can read the verdicts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAUSE = 2.0


def head(text: str) -> None:
    print()
    print("\033[1;36m══ " + text + " " + "═" * max(0, 72 - len(text)) + "\033[0m")
    time.sleep(0.4)


def dump(payload: dict, keys: tuple[str, ...]) -> None:
    for key in keys:
        if payload.get(key) is not None:
            value = payload[key]
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
                if len(value) > 96:
                    value = value[:93] + "..."
            print(f"  {key:<18} {value}")
    time.sleep(PAUSE)


def main() -> int:
    binary = os.environ.get("ZKAGENT_BIN", "/home/tango/.local/bin/zkagent")
    os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="cli-demo-")

    spec = importlib.util.spec_from_file_location("zk", ROOT / "__init__.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tools: dict = {}

    class Ctx:
        def register_tool(self, name, **kw):
            tools[name] = kw["handler"]

        def register_hook(self, event, fn, **kw):
            pass

    module.register(Ctx())

    delay = float(os.environ.get("CLI_DELAY", "0"))
    if delay:
        time.sleep(delay)

    print("\033[1mzkagent\033[0m  raw tool output, as an operator sees it")
    print(f"  binary  {binary}")
    print(subprocess.run([binary, "version"], capture_output=True, text=True, check=False).stdout.strip())

    head("1. the agent works")
    for name, args in (
        ("read_file", {"path": "/etc/hosts"}),
        ("terminal", {"command": "ls -la"}),
        ("web_search", {"query": "groth16"}),
        ("patch", {"path": "app.py", "old": "x", "new": "y"}),
        ("read_file", {"path": "/etc/hostname"}),
    ):
        module._record(name, args)
        print(f"  {name:<14} recorded")
        time.sleep(0.5)

    head("2. status")
    dump(json.loads(tools["zkagent_status"]({})), ("pending_actions", "runs", "slots_per_receipt"))

    head("3. attest")
    attest = json.loads(tools["zkagent_attest"]({"max_amount": 1000}))
    dump(attest, ("verdict", "actions", "total", "cap", "execution_id", "session"))

    head("4. verify")
    dump(json.loads(tools["zkagent_verify"]({})), ("verdict", "exit", "reason_code", "session"))

    head("5. disclose")
    dump(json.loads(tools["zkagent_disclose"]({"index": 2})), ("index", "recorded_tool", "raw"))

    run_dir = pathlib.Path(attest["proof"]).parent
    head("6. one byte changed")
    proof = json.loads((run_dir / "proof.json").read_text())

    def flip(node) -> bool:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and len(v) >= 16 and all(c in "0123456789abcdef" for c in v.lower()):
                    node[k] = ("0" if v[0] != "0" else "1") + v[1:]
                    return True
                if flip(v):
                    return True
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, str) and len(v) >= 16 and all(c in "0123456789abcdef" for c in v.lower()):
                    node[i] = ("0" if v[0] != "0" else "1") + v[1:]
                    return True
                if flip(v):
                    return True
        return False

    print(f"  flipped            {flip(proof)}")
    (run_dir / "proof-tampered.json").write_text(json.dumps(proof))
    run = subprocess.run(
        [binary, "--json", "verify", "--keys", str(module._keys_dir()),
         "--policy", str(run_dir / "policy.json"), "--receipt", str(run_dir / "receipt.json"),
         "--proof", str(run_dir / "proof-tampered.json")],
        capture_output=True, text=True, check=False,
    )
    print("  " + run.stdout.strip())
    print(f"  exit               {run.returncode}")
    time.sleep(PAUSE)

    print()
    print("\033[1mTotals are public. Actions stayed hidden. A changed byte was rejected.\033[0m")
    print()
    print("[end — press Enter to close]")
    try:
        input()
    except EOFError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
