#!/usr/bin/env python3
"""One command that answers: is this repository fit to be pinned and published?

    python3 scripts/check.py            # full run (needs zkagent + the hermes CLI)
    python3 scripts/check.py --no-binary  # skip the smoke run (no zkagent here)

It runs the same gates a reviewer runs, in the same order, and prints a table. Exit 1 if
any gate fails. This exists because "the tests passed" is a claim, and a claim needs a
command behind it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"nvapi-[A-Za-z0-9_-]{20,}",
    r"ghp_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]
BINARY_MAGIC = (b"\x7fELF", b"MZ", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\x00asm")


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, "PASS" if ok else "FAIL", detail))

    def skip(self, name: str, detail: str = "") -> None:
        # A gate that never ran is not a gate that passed. Reporting it as PASS was how CI
        # stayed green with no zkagent binary and no Hermes CLI on the runner.
        self.rows.append((name, "SKIP", detail))

    def report(self) -> int:
        width = max(len(n) for n, _, _ in self.rows) + 2
        failed = skipped = 0
        for name, mark, detail in self.rows:
            if mark == "FAIL":
                failed += 1
            elif mark == "SKIP":
                skipped += 1
            print(f"{mark:5} {name:<{width}} {detail}")
        print()
        summary = "OK" if failed == 0 else f"{failed} gate(s) failed"
        if skipped:
            summary += f" | {skipped} gate(s) SKIPPED, not passed"
        print(summary)
        return 0 if failed == 0 else 1


def run(cmd: list[str], timeout: int = 300, env: dict | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def check_secrets(result: Result) -> None:
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                hits.append(str(path.relative_to(ROOT)))
    result.add("no secrets in the tree", not hits, ", ".join(sorted(set(hits))[:3]))


def check_no_binaries(result: Result) -> None:
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            head = path.read_bytes()[:4]
        except OSError:
            continue
        if any(head.startswith(magic) for magic in BINARY_MAGIC):
            hits.append(str(path.relative_to(ROOT)))
    result.add("no bundled executables", not hits, ", ".join(hits[:3]))


def check_manifest_matches_code(result: Result) -> None:
    spec = importlib.util.spec_from_file_location("zkagent_attest", ROOT / "__init__.py")
    assert spec is not None and spec.loader is not None, "cannot load __init__.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Ctx:
        def __init__(self) -> None:
            self.tools: list[str] = []
            self.hooks: list[str] = []

        def register_tool(self, name, **kw) -> None:
            self.tools.append(name)

        def register_hook(self, event, fn, **kw) -> None:
            self.hooks.append(event)

    ctx = Ctx()
    module.register(ctx)
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    declared_tools = set(re.findall(r"^\s+- (\w+)$", manifest, re.M))
    ok = set(ctx.tools) <= declared_tools and all(h in manifest for h in ctx.hooks)
    missing = sorted(set(ctx.tools) - declared_tools)
    result.add(
        "manifest matches register()",
        ok,
        f"{len(ctx.tools)} tools, {len(ctx.hooks)} hook" + (f"; undeclared: {missing}" if missing else ""),
    )


def check_smoke(result: Result, binary: str | None) -> None:
    if not binary:
        result.skip("smoke test", "no zkagent binary; pass --binary PATH")
        return
    # Honour --binary PATH: smoke.py resolves ZKAGENT_BIN itself, so the flag has to reach
    # its environment rather than only acting as a presence check here.
    env = {**os.environ, "ZKAGENT_BIN": binary}
    code, out = run([sys.executable, str(ROOT / "tests" / "smoke.py")], timeout=300, env=env)
    tail = [line for line in out.strip().splitlines() if line][-1] if out.strip() else ""
    result.add("smoke test", code == 0 and "OK" in out, tail[:90])



def check_version(result: Result) -> None:
    """The plugin version, the README badge and the changelog must agree.

    Three places state the same number and nothing tied them together: a release could bump
    plugin.yaml alone and the README would keep advertising the previous version, which is exactly
    what a reviewer reads first.
    """
    import re as _re

    root = ROOT
    manifest = _re.search(r"^version:\s*(\S+)", (root / "plugin.yaml").read_text(encoding="utf-8"), _re.M)
    changelog = _re.search(r"^## (\d+\.\d+\.\d+)$", (root / "CHANGELOG.md").read_text(encoding="utf-8"), _re.M)
    badge = _re.search(r"plugin-(\d+\.\d+\.\d+)-blue", (root / "README.md").read_text(encoding="utf-8"))
    if not (manifest and changelog and badge):
        result.add("version consistency", False, "plugin.yaml, README badge or CHANGELOG.md missing a version")
        return
    versions = {"plugin.yaml": manifest.group(1).strip('"'), "CHANGELOG.md": changelog.group(1),
                "README.md": badge.group(1)}
    ok = len(set(versions.values())) == 1
    detail = " · ".join(f"{k} {v}" for k, v in versions.items())
    result.add("version consistency", ok, detail)

def check_guard_tests(result: Result) -> None:
    """Failure-path guards. No binary needed, so this gate can never be skipped."""
    code, out = run([sys.executable, str(ROOT / "tests" / "test_guards.py")], timeout=300)
    tail = [line for line in out.strip().splitlines() if line][-1] if out.strip() else ""
    result.add("guard tests", code == 0 and "OK" in out, tail[:90])


def check_hermes_gates(result: Result) -> None:
    if not shutil.which("hermes"):
        result.skip("hermes validate", "no hermes CLI on this machine")
        result.skip("hermes doctor", "no hermes CLI on this machine")
        return
    code, out = run(["hermes", "plugins", "validate", str(ROOT), "--install-deps"], timeout=600)
    result.add("hermes validate", code == 0 and "Validation passed" in out, "catalog checks")
    code, out = run(["hermes", "plugins", "doctor", str(ROOT)], timeout=300)
    detail = next((ln.strip() for ln in out.splitlines() if "registrations:" in ln), "")
    result.add("hermes doctor", code == 0 and "OK:" in out, detail[:60])


def check_docs(result: Result) -> None:
    required = ["README.md", "LICENSE", "CONSTITUTION.md", "AGENTS.md", "SECURITY.md",
                "docs/ARCHITECTURE.md", "docs/TESTING.md", "skills/zkagent-attest/SKILL.md"]
    missing = [name for name in required if not (ROOT / name).is_file()]
    result.add("docs present", not missing, ", ".join(missing) or f"{len(required)} files")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", default=os.environ.get("ZKAGENT_BIN") or shutil.which("zkagent"))
    parser.add_argument("--no-binary", action="store_true")
    args = parser.parse_args()

    result = Result()
    check_secrets(result)
    check_no_binaries(result)
    check_manifest_matches_code(result)
    check_docs(result)
    check_version(result)
    check_guard_tests(result)
    check_smoke(result, None if args.no_binary else args.binary)
    check_hermes_gates(result)
    return result.report()


if __name__ == "__main__":
    sys.exit(main())
