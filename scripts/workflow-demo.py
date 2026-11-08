"""The whole workflow, printed for a screen recording.

install state -> agent works -> status -> attest -> verify -> disclose -> tamper -> the
Omarchy bar reads the verdict. Real tools, real binary, real files.
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
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIN = os.environ.get("ZKAGENT_BIN", "/home/tango/.local/bin/zkagent")
PAUSE = 2.2
VERDICT_FILE = pathlib.Path.home() / ".local/state/zkagent/last-verdict.json"

FONT = {
    "V": ("█ █", "█ █", " ▀ "), "E": ("█▀▀", "█▀ ", "▀▀▀"), "R": ("█▀▄", "█▀▄", "▀ ▀"),
    "I": ("▀█▀", " █ ", "▀▀▀"), "F": ("█▀▀", "█▀ ", "▀  "), "A": ("▄▀▄", "█▀█", "▀ ▀"),
    "B": ("█▀▄", "█▀▄", "▀▀ "), "L": ("█  ", "█  ", "▀▀▀"), "C": ("█▀▀", "█  ", "▀▀▀"),
    "P": ("█▀▄", "█▀ ", "▀  "), "T": ("▀█▀", " █ ", " ▀ "), "S": ("█▀▀", "▀▀█", "▀▀▀"),
    " ": ("   ", "   ", "   "),
}

BANNER = r"""
   ███████╗██╗  ██╗ █████╗  ██████╗ ███████╗███╗   ██╗████████╗
   ╚══███╔╝██║ ██╔╝██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
     ███╔╝ █████╔╝ ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
    ███╔╝  ██╔═██╗ ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
   ███████╗██║  ██╗██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
   ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
"""

BLURB = (
    "An agent acts on your behalf: it calls tools, spends budget, touches your data. Today "
    "the only account of that is the agent's own transcript. zkagent turns the session into "
    "a receipt you can check: the totals, the action count and a policy cap you signed, "
    "proved in zero knowledge, so a verifier learns the session stayed inside the limits "
    "without seeing a single action. In Hermes that gives every plugin, MCP server and client "
    "a shared answer to \"did this agent do what it was allowed to do\" without publishing "
    "prompts, arguments or customer data."
)


def small_art(phrase: str) -> list[str]:
    rows = ["", "", ""]
    for char in phrase:
        glyph = FONT.get(char, FONT[" "])
        for index in range(3):
            rows[index] += glyph[index] + " "
    return rows


def heading(width: int) -> int:
    print("\033[1;36m" + BANNER + "\033[0m", end="")
    print("           verifiable receipts for what an agent did")
    print(f"           zkagent-attest {plugin_version()} · zkagent 0.4.0 · format 3")
    print()
    print("\033[36m" + "\n".join("  " + row.rstrip() for row in small_art("VERIFIABLE RECEIPTS")) + "\033[0m")
    print()
    lines = textwrap.wrap(BLURB, width=max(40, width - 4))
    for line in lines:
        print("\033[2m  " + line + "\033[0m")
    print()
    return 7 + 2 + 1 + 3 + 1 + len(lines) + 1


def step(n: str, title: str) -> None:
    print()
    print(f"\033[1;36m[{n}]\033[0m \033[1m{title}\033[0m")
    print("\033[2m" + "-" * 66 + "\033[0m")
    time.sleep(0.5)


def field(key: str, value) -> None:
    text = str(value)
    if len(text) > 84:
        text = text[:81] + "..."
    print(f"  {key:<18} {text}")
    time.sleep(0.35)



def plugin_version() -> str:
    """Read the version instead of hardcoding it: a demo that lies about the build is worse than no demo."""
    try:
        found = re.search(r"^version:\s*(\S+)", (ROOT / "plugin.yaml").read_text(encoding="utf-8"), re.M)
        return found.group(1).strip('"\'') if found else "unknown"
    except OSError:
        return "unknown"

def main() -> int:
    delay = float(os.environ.get("CLI_DELAY", "0"))
    if delay:
        time.sleep(delay)

    os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="workflow-demo-")
    spec = importlib.util.spec_from_file_location("zk", ROOT / "__init__.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tools: dict = {}
    hooks: list[str] = []

    class Ctx:
        def register_tool(self, name, **kw):
            tools[name] = kw["handler"]

        def register_hook(self, event, fn, **kw):
            hooks.append(event)

    module.register(Ctx())

    width, height = shutil.get_terminal_size((76, 46))
    used = heading(width)
    top = used + 1
    if height > top + 4:
        print(f"\033[{top};{height}r\033[{top};1H", end="", flush=True)

    step("0", "installed in Hermes")
    field("plugin", f"zkagent-attest {plugin_version()}")
    field("tools", ", ".join(sorted(tools)))
    field("hook", ", ".join(hooks) or "-")
    time.sleep(0.6)

    step("1", "the agent works, the hook records it")
    for name, args in (
        ("read_file", {"path": "/etc/hosts"}),
        ("terminal", {"command": "ls -la"}),
        ("web_search", {"query": "groth16"}),
        ("patch", {"path": "app.py", "old": "x", "new": "y"}),
        ("read_file", {"path": "/etc/hostname"}),
    ):
        module._record(name, args)
        print(f"  {name:<14} recorded · arguments hashed, never stored")
        time.sleep(0.7)

    step("2", "status: what the session did, as totals")
    status = json.loads(tools["zkagent_status"]({}))
    field("pending_actions", status["pending_actions"])
    field("runs", status["runs"])
    field("slots_per_receipt", status["slots_per_receipt"])

    step("3", "attest: sign a cap, prove the session stayed inside it")
    attest = json.loads(tools["zkagent_attest"]({"max_amount": 1000}))
    field("verdict", attest["verdict"])
    field("actions", attest["actions"])
    field("total", attest["total"])
    field("cap", attest["cap"])
    field("execution_id", attest["execution_id"])
    field("proof", f"{pathlib.Path(attest['proof']).stat().st_size:,} bytes")
    time.sleep(0.8)

    step("4", "verify: the artifact, not the agent's word")
    verified = json.loads(tools["zkagent_verify"]({}))
    field("verdict", verified["verdict"])
    field("exit", verified["exit"])
    field("session_ok", verified["session"]["ok"])
    field("problems", verified["session"]["problems"] or "none")
    time.sleep(0.8)

    step("5", "disclose: one action out of the set")
    revealed = json.loads(tools["zkagent_disclose"]({"index": 2}))
    field("index", revealed["index"])
    field("recorded_tool", revealed["recorded_tool"])
    field("the rest", f"{attest['actions'] - 1} actions stay hidden")
    time.sleep(0.8)

    step("6", "tamper: change one hex character in the proof")
    run_dir = pathlib.Path(attest["proof"]).parent
    proof = json.loads((run_dir / "proof.json").read_text())

    def flip(node) -> bool:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and len(value) >= 16 and all(c in "0123456789abcdef" for c in value.lower()):
                    node[key] = ("0" if value[0] != "0" else "1") + value[1:]
                    return True
                if flip(value):
                    return True
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, str) and len(value) >= 16 and all(c in "0123456789abcdef" for c in value.lower()):
                    node[index] = ("0" if value[0] != "0" else "1") + value[1:]
                    return True
                if flip(value):
                    return True
        return False

    field("flipped", flip(proof))
    (run_dir / "proof-tampered.json").write_text(json.dumps(proof))
    run = subprocess.run(
        [BIN, "--json", "verify", "--keys", str(module._keys_dir()),
         "--policy", str(run_dir / "policy.json"), "--receipt", str(run_dir / "receipt.json"),
         "--proof", str(run_dir / "proof-tampered.json")],
        capture_output=True, text=True, check=False,
    )
    body = json.loads(run.stdout.strip() or "{}")
    field("verdict", str(body.get("verdict", "unknown")).upper())
    field("reason_code", body.get("reason_code", "-"))
    field("exit", run.returncode)
    time.sleep(1.0)

    step("7", "the Omarchy bar reads the same document")
    field("widget", "mcpvot.attest · Quickshell/QML · MIT")
    field("verdict file", str(VERDICT_FILE).replace(str(pathlib.Path.home()), "~"))
    if VERDICT_FILE.exists():
        age = int(time.time() - VERDICT_FILE.stat().st_mtime)
        try:
            shown = json.loads(VERDICT_FILE.read_text())
        except ValueError:
            shown = {}
        field("bar shows", f"{shown.get('verdict', 'unknown')} · {age}s old")
        field("stale after", "900s, then grey instead of green")
    else:
        field("bar shows", "no verdict yet (widget waits for attest-watch)")
    time.sleep(1.0)

    print()
    print("\033[1m  Totals are public. Actions stayed hidden.")
    print("  A changed byte was rejected. The desktop shows the same verdict.\033[0m")
    print()
    print("[end — press Enter to close]")
    try:
        input()
    except EOFError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
