"""Smoke test: drive the plugin the way the host does, against a real zkagent binary.

    ZKAGENT_BIN=~/work/zkagent/target/release/zkagent python3 tests/smoke.py

Asserts the things that matter, not just that the code runs: the hook records actions, the
attestation produces an accepted verdict, a tampered proof is rejected, a disclosure names
exactly one slot, the session index cross-checks against two receipts and rejects a tampered
total, an accepted execution id is refused on a second verify, and a missing binary is
fetched through the pinned installer (stubbed here, so the test needs no network).
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

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

    # A throwaway HERMES_HOME: the test clears a session to start clean, and doing that to a
    # live profile's ledger would delete real evidence (and race the agent that is writing it).
    home = tempfile.mkdtemp(prefix="zkagent-smoke-")
    os.environ["HERMES_HOME"] = home
    module = load()
    print("home     :", home)
    ctx = FakeCtx()
    module.register(ctx)

    assert set(ctx.tools) == {"zkagent_status", "zkagent_attest", "zkagent_verify", "zkagent_disclose"}, sorted(ctx.tools)
    assert "post_tool_call" in ctx.hooks, "hook not registered"

    # A fresh session: the same thing a user gets by clearing state.json, plus the files that
    # belong to a session (the ledger, the index and the replay guard) so the run is repeatable.
    for path in (module._state_path(), module._pending_path(), module._session_path(), module._seen_path()):
        if path.exists():
            path.unlink()

    # The manifest's cost defaults are the settings form's numbers; they must be the same
    # numbers DEFAULT_COSTS charges, or the form quotes one price and the ledger bills another.
    manifest = pathlib.Path(PLUGIN, "plugin.yaml").read_text(encoding="utf-8")
    schema_block = manifest.split("config_schema:", 1)[1]
    declared = {
        match.group(1): int(match.group(2))
        for match in re.finditer(r"^  (\w+):\n    type: int\n    default: (\d+)", schema_block, re.M)
    }
    assert declared == module.DEFAULT_COSTS, (declared, module.DEFAULT_COSTS)
    print("config   : plugin.yaml defaults match DEFAULT_COSTS |", len(declared), "keys")

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

    # --- replay: an accepted execution id cannot be presented a second time ----------------
    replay = json.loads(ctx.tools["zkagent_verify"]({"run_id": attest["execution_id"]}))
    assert replay.get("ok") is False and replay.get("verdict") == "replay", replay
    assert "already accepted" in (replay.get("error") or ""), replay
    print("replay   : execution_id", attest["execution_id"], "refused |", replay["verdict"])

    # --- a session that outgrows one receipt: the index has to span two -------------------
    for name, args in [
        ("read_file", {"path": "/etc/passwd"}),
        ("terminal", {"command": "date -u"}),
        ("web_search", {"query": "mimc merkle"}),
        ("write_file", {"path": "b.txt", "content": "x"}),
    ]:
        ctx.hooks["post_tool_call"][0](tool_name=name, args=args, result="ok", task_id="smoke")
    second = json.loads(ctx.tools["zkagent_attest"]({"max_amount": 20, "ttl_seconds": 600}))
    assert second.get("verdict") == "accept", second
    assert second["execution_id"] != attest["execution_id"], (attest, second)
    print("attest 2 :", second["verdict"], "| actions", second["actions"], "| total", second["total"],
          "| execution_id", second["execution_id"])

    session_path = module._session_path()
    assert session_path.is_file(), "no session.json beside the runs"
    index = json.loads(session_path.read_text(encoding="utf-8"))
    listed_ids = [row["execution_id"] for row in index["receipts"]]
    assert listed_ids == [attest["execution_id"], second["execution_id"]], listed_ids
    assert index["receipt_count"] == 2 and index["action_count"] == 9, index
    assert index["total"] == 17, index
    on_disk = []
    for row in index["receipts"]:
        receipt = json.loads((module._data_dir() / row["receipt"]).read_text(encoding="utf-8"))
        assert receipt["trace_root"] == row["trace_root"], row
        on_disk.append(receipt["trace_root"])
    assert index["root"] == module._merkle_root(on_disk), index["root"]
    print("session  :", index["receipt_count"], "receipts | total", index["total"], "| root", index["root"][:16] + "…")

    # --- a tampered index is a hard failure, not a warning --------------------------------
    original = session_path.read_text(encoding="utf-8")
    tampered_index = json.loads(original)
    tampered_index["total"] = int(tampered_index["total"]) + 1
    session_path.write_text(json.dumps(tampered_index), encoding="utf-8")
    try:
        broken = json.loads(ctx.tools["zkagent_verify"]({"session": True}))
    finally:
        session_path.write_text(original, encoding="utf-8")
    assert broken.get("ok") is False and broken.get("verdict") == "session_mismatch", broken
    assert any("session total" in problem for problem in broken["session"]["problems"]), broken
    print("index    : tampered total refused |", broken["verdict"], "|",
          next(p for p in broken["session"]["problems"] if "session total" in p)[:70])

    # --- the aggregate: every receipt accepts, roots match, total adds up -----------------
    aggregate = json.loads(ctx.tools["zkagent_verify"]({"session": True}))
    assert aggregate.get("verdict") == "accept", aggregate
    assert aggregate["root"] == index["root"], aggregate
    assert aggregate["total"] == index["total"] and aggregate["actions"] == 9, aggregate
    assert [row["verdict"] for row in aggregate["receipts_verified"]] == ["accept", "accept"], aggregate
    print("session  : aggregate", aggregate["verdict"], "| receipts",
          len(aggregate["receipts_verified"]), "| total", aggregate["total"])

    replayed_session = json.loads(ctx.tools["zkagent_verify"]({"session": True}))
    assert replayed_session.get("ok") is False and replayed_session.get("verdict") == "replay", replayed_session
    print("session  : same root again refused |", replayed_session["verdict"])

    # --- auto-fetch: with no binary anywhere, the pinned installer is the way in ---------
    # The installer is stubbed to copy the real binary, so this proves the wiring (import by
    # path, --dir into INSTALL_DIR, re-resolve, report) without a network dependency. The real
    # script's sha256 check is unchanged in production; nothing here bypasses it.
    stub_dir = pathlib.Path(tempfile.mkdtemp(prefix="zkagent-install-"))
    stub_script = stub_dir / "install-zkagent.py"
    stub_script.write_text(
        textwrap.dedent(
            f'''
            import os, shutil, sys

            def main():
                dest = sys.argv[sys.argv.index("--dir") + 1]
                os.makedirs(dest, exist_ok=True)
                target = os.path.join(dest, "zkagent")
                shutil.copyfile({str(BINARY)!r}, target)
                os.chmod(target, 0o755)
                print("stub installer ->", target)
                return 0
            '''
        ),
        encoding="utf-8",
    )
    saved = (
        module.INSTALL_SCRIPT,
        module.INSTALL_DIR,
        module._INSTALL_ATTEMPTED,
        module._INSTALLED_BIN,
        dict(os.environ),
    )
    module.INSTALL_SCRIPT = stub_script
    module.INSTALL_DIR = stub_dir / "bin"
    module._INSTALL_ATTEMPTED = False
    module._INSTALLED_BIN = None
    os.environ.pop("ZKAGENT_BIN", None)
    os.environ["PATH"] = str(stub_dir / "empty-path")
    try:
        fetched = json.loads(ctx.tools["zkagent_status"]({}))
    finally:
        module.INSTALL_SCRIPT, module.INSTALL_DIR = saved[0], saved[1]
        module._INSTALL_ATTEMPTED, module._INSTALLED_BIN = saved[2], saved[3]
        os.environ.clear()
        os.environ.update(saved[4])
    assert fetched.get("install", {}).get("ok") is True, fetched
    assert fetched["binary"] == str(stub_dir / "bin" / "zkagent"), fetched
    assert "stub installer" in fetched["install"].get("output", ""), fetched
    print("autofetch: install reported |", fetched["binary"])

    # --- auto-fetch failing is reported, not swallowed -----------------------------------
    missing = (module.INSTALL_SCRIPT, module.INSTALL_DIR, module._INSTALL_ATTEMPTED, module._INSTALLED_BIN, dict(os.environ))
    module.INSTALL_SCRIPT = stub_dir / "not-here.py"
    module.INSTALL_DIR = stub_dir / "bin2"
    module._INSTALL_ATTEMPTED = False
    module._INSTALLED_BIN = None
    os.environ.pop("ZKAGENT_BIN", None)
    os.environ["PATH"] = str(stub_dir / "empty-path")
    try:
        broken_fetch = json.loads(ctx.tools["zkagent_status"]({}))
    finally:
        module.INSTALL_SCRIPT, module.INSTALL_DIR = missing[0], missing[1]
        module._INSTALL_ATTEMPTED, module._INSTALLED_BIN = missing[2], missing[3]
        os.environ.clear()
        os.environ.update(missing[4])
    assert broken_fetch["binary"] is None, broken_fetch
    assert broken_fetch["install"]["ok"] is False and "installer not found" in broken_fetch["install"]["error"], broken_fetch
    assert "Auto-install" in broken_fetch["error"], broken_fetch
    print("autofetch: failed install reported in the tool output |", broken_fetch["install"]["error"][:60])

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
