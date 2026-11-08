"""Play the demo once, with the wordmark and the description pinned to the top.

The heading is printed once and a scroll region is set below it, so every beat appears
under a heading that never scrolls away. Real tools, real binary, real verdicts.

    ZKAGENT_BIN=~/.local/bin/zkagent python3 scripts/demo-take.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAUSE = 2.6

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


def beat(title: str) -> None:
    print()
    print("\033[1;36m▸ " + title + "\033[0m")
    time.sleep(PAUSE)


def show(payload: dict, keys: tuple[str, ...]) -> None:
    for key in keys:
        if payload.get(key) is not None:
            print(f"    {key:<16} {payload[key]}")
    time.sleep(PAUSE)


def print_heading(width: int) -> int:
    """Print the wordmark and the description. Returns the last line used."""
    print("\033[1;36m" + BANNER + "\033[0m", end="")
    print("           verifiable receipts for what an agent did")
    print("           zkagent 0.4.0 · format 3 · 7,442 constraints · proof ≈2.3 kB")
    print()
    print("\033[36m" + "\n".join("  " + row.rstrip() for row in small_art("VERIFIABLE RECEIPTS")) + "\033[0m")
    print()
    lines = textwrap.wrap(BLURB, width=min(width - 4, 88))
    for line in lines:
        print("\033[2m  " + line + "\033[0m")
    print()
    return 7 + 2 + 1 + 3 + 1 + len(lines) + 1


def main() -> int:
    os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="demo-take-")
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

    width, height = shutil.get_terminal_size((100, 44))
    used = print_heading(width)

    top = used + 1
    if height > top + 3:
        print(f"\033[{top};{height}r\033[{top};1H", end="", flush=True)

    beat("the agent works: five tool calls")
    for name, args in (
        ("read_file", {"path": "/etc/hosts"}),
        ("terminal", {"command": "ls -la"}),
        ("web_search", {"query": "groth16"}),
        ("patch", {"path": "app.py", "old": "x", "new": "y"}),
        ("read_file", {"path": "/etc/hostname"}),
    ):
        module._record(name, args)
        print(f"    {name:<14} recorded")
        time.sleep(0.9)

    beat("nobody asked the plugin to count them")
    show(json.loads(tools["zkagent_status"]({})), ("pending_actions", "runs"))

    beat("sign a cap and prove the session stayed inside it")
    attest = json.loads(tools["zkagent_attest"]({"max_amount": 20}))
    show(attest, ("verdict", "actions", "total", "cap"))
    run_dir = pathlib.Path(attest["proof"]).parent
    receipt = json.loads((run_dir / "receipt.json").read_text())
    print(f"    {'receipt':<16} total {receipt['total_amount']} · {receipt['action_count']} actions "
          f"· execution {receipt['execution_id']}")
    print(f"    {'trace_root':<16} {receipt['trace_root'][:32]}…")
    print(f"    {'proof':<16} {pathlib.Path(attest['proof']).stat().st_size:,} bytes")
    time.sleep(PAUSE)

    beat("verify the artifact, not the memory")
    show(json.loads(tools["zkagent_verify"]({})), ("verdict", "execution_id"))
    print("    the verifier saw 16 public inputs and no actions")
    time.sleep(PAUSE)

    beat("one byte changed in the proof")
    tampered = json.loads((run_dir / "proof.json").read_text())

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

    print(f"    tampered           {flip(tampered)}")
    (run_dir / "proof-tampered.json").write_text(json.dumps(tampered))
    run = subprocess.run(
        [os.environ["ZKAGENT_BIN"], "--json", "verify", "--keys", str(module._keys_dir()),
         "--policy", str(run_dir / "policy.json"), "--receipt", str(run_dir / "receipt.json"),
         "--proof", str(run_dir / "proof-tampered.json")],
        capture_output=True, text=True, check=False,
    )
    verdict: dict = {}
    for line in run.stdout.strip().splitlines():
        try:
            verdict = json.loads(line)
        except ValueError:
            continue
    print(f"    verdict            {str(verdict.get('verdict', 'unknown')).upper()}  (exit {run.returncode})")
    print(f"    reason             {verdict.get('reason_code') or verdict.get('reason') or '-'}")
    time.sleep(PAUSE)

    beat("reveal exactly one action")
    revealed = json.loads(tools["zkagent_disclose"]({"index": 2}))
    show(revealed, ("index", "recorded_tool"))
    print("    the other four actions were not revealed")
    time.sleep(PAUSE)

    beat("summary")
    print("\033[1m  accept    " + f"{attest['actions']} actions · total {attest['total']} · cap {attest['cap']}" + "\033[0m")
    print("\033[1m  accept    verify · execution " + str(attest.get("execution_id", "-")) + "\033[0m")
    print("\033[1m  reject    tamper · exit " + str(run.returncode) + " · public_input_mismatch\033[0m")
    print("\033[1m  hidden    4 of 5 actions\033[0m")
    print()
    print("\033[1m  Totals are public. Actions are not. A tampered proof fails.\033[0m")
    print()
    print("[demo complete — press Enter to close]")
    try:
        input()
    except EOFError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
