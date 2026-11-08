#!/usr/bin/env python3
"""Compare docs/ARTIFACTS.md with a live run.

The documented keys and the real keys are read from the same files the plugin writes, so a field
added in code and not in the doc fails here rather than surprising whoever wrote a reader.

    python3 scripts/check_artifacts.py [--no-binary]
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs/ARTIFACTS.md"
SHAPE = re.compile(r"^keys(?:\.([a-z_]+)\[\])?: (.+)$", re.M)


def documented() -> dict[str, dict[str, list[str]]]:
    """{artifact: {path: [keys]}} from the fenced shape lines."""
    shapes: dict[str, dict[str, list[str]]] = {}
    section = ""
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## `"):
            section = line.split("`")[1]
            shapes[section] = {}
        match = SHAPE.match(line)
        if match and section:
            path = match.group(1) or "$"
            shapes[section][path] = sorted(k.strip() for k in match.group(2).split(","))
    return shapes


def live() -> dict[str, dict[str, list[str]]]:
    """The same structure, from a real run in a throwaway HERMES_HOME."""
    home = Path(tempfile.mkdtemp(prefix="artifacts-"))
    os.environ["HERMES_HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("zk_artifacts", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools: dict = {}

    class Ctx:
        def register_tool(self, name, **kw):
            tools[name] = kw["handler"]

        def register_hook(self, event, fn, **kw):
            pass

    module.register(Ctx())
    for name, args in (("read_file", {"path": "/a"}), ("terminal", {"command": "ls"}), ("web_search", {"query": "q"})):
        module._record(name, args)
    attest = json.loads(tools["zkagent_attest"]({"max_amount": 100}))
    json.loads(tools["zkagent_verify"]({}))

    data = home / "plugin-data" / "zkagent-attest"
    out: dict[str, dict[str, list[str]]] = {}
    for name in ("state.json", "seen.json", "session.json"):
        doc = json.loads((data / name).read_text(encoding="utf-8"))
        entry = {"$": sorted(doc.keys())}
        for key, value in doc.items():
            if isinstance(value, dict) and value:
                first = next(iter(value.values()))
                if isinstance(first, dict):
                    entry[key] = sorted(first.keys())
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                entry[key] = sorted(value[0].keys())
        out[name] = entry

    run = data / "runs" / f"run-{attest['execution_id']}"
    policy = json.loads((run / "policy.json").read_text(encoding="utf-8"))["policy"]
    out["runs/run-N/"] = {
        "policy.json": sorted(policy.keys()),
        "receipt.json": sorted(json.loads((run / "receipt.json").read_text(encoding="utf-8")).keys()),
        "proof.json": sorted(json.loads((run / "proof.json").read_text(encoding="utf-8")).keys()),
    }
    return out


def main() -> int:
    if "--no-binary" in sys.argv[1:]:
        print("artifacts: SKIP (no binary)")
        return 0
    want, have = documented(), live()
    problems: list[str] = []
    for artifact, paths in want.items():
        actual = have.get(artifact)
        if actual is None:
            problems.append(f"{artifact}: documented, never produced")
            continue
        for path, keys in paths.items():
            got = actual.get(path)
            if got is None:
                continue
            if got != keys:
                missing = [k for k in got if k not in keys]
                extra = [k for k in keys if k not in got]
                problems.append(f"{artifact} {path}: doc{' missing ' + ','.join(missing) if missing else ''}"
                                f"{' extra ' + ','.join(extra) if extra else ''}")
    if problems:
        print("artifacts: FAIL docs/ARTIFACTS.md disagrees with the files the plugin writes")
        for problem in problems:
            print("  -", problem)
        return 1
    checked = sum(len(paths) for paths in want.values())
    print(f"artifacts: OK ({checked} shapes match a live run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
