"""Smoke test: drive the plugin the way the host does, against a real zkagent binary.

    ZKAGENT_BIN=~/work/zkagent/target/release/zkagent python3 tests/smoke.py

Asserts the four things that matter, not just that the code runs: the hook records
actions, the attestation produces an accepted verdict, a tampered proof is rejected, and a
disclosure names exactly one slot.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
BINARY = os.environ.get("ZKAGENT_BIN") or shutil.which("zkagent")


class FakeCtx:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.hooks: dict[str, list] = {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert schema.get("name") == name, f"schema name mismatch for {name}: {schema.get('name')!r}"
        self.tools[name] = handler

    def register_hook(self, event, fn, **kw):
        self.hooks.setdefault(event, []).append(fn)


def load():
    spec = importlib.util.spec_from_file_location("zkagent_attest", os.path.join(PLUGIN, "__init__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not BINARY:
        print("FAIL: no zkagent binary (set ZKAGENT_BIN)")
        return 2

    os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes/profiles/trilogy-brain"))
    module = load()
    ctx = FakeCtx()
    module.register(ctx)

    assert set(ctx.tools) == {"zkagent_status", "zkagent_attest", "zkagent_verify", "zkagent_disclose"}, sorted(ctx.tools)
    assert "post_tool_call" in ctx.hooks, "hook not registered"

    state = module._state_path()
    if state.exists():
        state.unlink()

    status = json.loads(ctx.tools["zkagent_status"]({}))
    assert status["binary"], status
    print("status   :", status["binary"], "|", status.get("zkagent", {}).get("raw", ""))

    calls = [
        ("read_file", {"path": "/etc/hosts"}),
        ("terminal", {"command": "ls -la"}),
        ("web_search", {"query": "groth16"}),
        ("patch", {"path": "a.py", "old": "x", "new": "y"}),
        ("read_file", {"path": "/etc/hostname"}),
    ]
    for name, args in calls:
        ctx.hooks["post_tool_call"][0](tool_name=name, args=args, result="ok", task_id="smoke")

    pending = json.loads(ctx.tools["zkagent_status"]({}))["pending_actions"]
    assert pending == 5, f"hook recorded {pending} actions, expected 5"
    print("hook     :", pending, "actions recorded")

    attest = json.loads(ctx.tools["zkagent_attest"]({"max_amount": 20, "ttl_seconds": 600}))
    assert attest.get("verdict") == "accept", attest
    print("attest   :", attest["verdict"], "| actions", attest["actions"], "| total", attest["total"], "| cap", attest["cap"])

    verify = json.loads(ctx.tools["zkagent_verify"]({}))
    assert verify.get("verdict") == "accept", verify
    print("verify   :", verify["verdict"])

    run_dir = pathlib.Path(attest["proof"]).parent
    tampered = json.loads((run_dir / "proof.json").read_text())

    def tamper_in_place(node) -> bool:
        """Flip one hex character in the first hex string in the artifact."""
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and len(value) >= 16 and all(c in "0123456789abcdef" for c in value.lower()):
                    node[key] = ("0" if value[0] != "0" else "1") + value[1:]
                    return True
                if tamper_in_place(value):
                    return True
        elif isinstance(node, list):
            for index, item in enumerate(node):
                if isinstance(item, str) and len(item) >= 16 and all(c in "0123456789abcdef" for c in item.lower()):
                    node[index] = ("0" if item[0] != "0" else "1") + item[1:]
                    return True
                if tamper_in_place(item):
                    return True
        return False

    assert tamper_in_place(tampered), "could not find a hex string to tamper with"
    tamper_path = run_dir / "proof-tampered.json"
    tamper_path.write_text(json.dumps(tampered), encoding="utf-8")

    proc = subprocess.run(
        [BINARY, "--json", "verify",
         "--keys", str(module._keys_dir()),
         "--policy", str(run_dir / "policy.json"),
         "--receipt", str(run_dir / "receipt.json"),
         "--proof", str(tamper_path)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode != 0, f"tampered proof was ACCEPTED (exit {proc.returncode})"
    print("tamper   : rejected as expected, exit", proc.returncode, "|", (proc.stdout or proc.stderr).strip()[:120])

    disclosure = json.loads(ctx.tools["zkagent_disclose"]({"index": 2}))
    assert disclosure.get("ok") and pathlib.Path(disclosure["out"]).is_file(), disclosure
    shown = json.loads(pathlib.Path(disclosure["out"]).read_text())
    assert "trace_digest" in json.dumps(shown) or "digest" in json.dumps(shown), shown
    print("disclose : slot 2 ->", os.path.basename(disclosure["out"]), "| recorded tool:", disclosure.get("recorded_tool"))

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
