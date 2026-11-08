"""Print the raw JSON of every zkagent tool result, for a screen recording.

The chat UI collapses tool output into one-line chips. This prints the same documents
in full, one screen at a time, so a viewer can read the verdicts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAUSE = 2.4
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
    """Print the wordmark and the description. Returns the last line used."""
    import textwrap
    print("\033[1;36m" + BANNER + "\033[0m", end="")
    print("           verifiable receipts for what an agent did")
    print("           zkagent 0.4.0 · format 3 · 7,442 constraints · proof ≈2.3 kB")
    print()
    print("\033[36m" + "\n".join("  " + row.rstrip() for row in small_art("VERIFIABLE RECEIPTS")) + "\033[0m")
    print()
    lines = textwrap.wrap(BLURB, width=max(40, width - 4))
    for line in lines:
        print("\033[2m  " + line + "\033[0m")
    print()
    return 7 + 2 + 1 + 3 + 1 + len(lines) + 1




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



def plugin_version() -> str:
    """Read the version instead of hardcoding it: a demo that lies about the build is worse than no demo."""
    try:
        found = re.search(r"^version:\s*(\S+)", (ROOT / "plugin.yaml").read_text(encoding="utf-8"), re.M)
        return found.group(1).strip('"\'') if found else "unknown"
    except OSError:
        return "unknown"

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

    import shutil

    width, height = shutil.get_terminal_size((80, 50))
    used = heading(width)
    top = used + 1
    if height > top + 4:
        print(f"\033[{top};{height}r\033[{top};1H", end="", flush=True)

    print(f"\033[2m  binary {binary}\033[0m")
    print("  " + subprocess.run([binary, "version"], capture_output=True, text=True, check=False).stdout.strip())

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
        time.sleep(0.8)

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
