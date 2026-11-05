#!/usr/bin/env python3
"""Measure the cost of the post_tool_call hook, the way a user feels it.

    python3 scripts/bench.py                 # 200-byte and 200 KB args, 200 calls
    python3 scripts/bench.py --calls 500 --arg-bytes 200
    python3 scripts/bench.py --breakdown     # per-component cost inside the hook

The hook runs synchronously on every tool call the agent makes, so the number that matters
is wall time per call, not lines of code. State is written to a throwaway HERMES_HOME, so a
run never touches the ledger a real session is using.

`--repeat` takes the median of N full runs; a loaded machine can move a single run by 50%,
and a median is the honest summary of that.
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import os
import pathlib
import statistics
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_plugin():
    spec = importlib.util.spec_from_file_location("zkagent_attest_bench", ROOT / "__init__.py")
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load __init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_args(size: int, index: int = 0) -> dict:
    """A tool-call payload of roughly `size` bytes once canonicalised."""
    head = {"path": f"/tmp/file-{index}.txt"}
    filler = "x" * max(0, size - len(json.dumps(head)))
    return {"path": f"/tmp/file-{index}.txt", "body": filler}


def ledger_bytes(plugin) -> int:
    """Everything the plugin keeps for one session: header plus pending rows."""
    total = 0
    for path in (plugin._state_path(), plugin._state_path().parent / "pending.jsonl"):
        if path.exists():
            total += path.stat().st_size
    return total


def time_hook(calls: int, arg_bytes: int) -> tuple[float, int]:
    """ms per call over `calls` hook invocations, and the bytes on disk afterwards."""
    home = pathlib.Path(tempfile.mkdtemp(prefix="zkbench-"))
    old = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    try:
        plugin = load_plugin()
        state = plugin._state_path()
        args = make_args(arg_bytes)
        tools = ("read_file", "terminal", "web_search", "patch", "web_extract")
        hook = plugin._on_post_tool_call
        hook(tool_name="read_file", args=args, result="ok", task_id="warmup")  # warm caches
        if state.exists():
            state.unlink()
        gc.collect()
        started = time.perf_counter()
        for index in range(calls):
            hook(tool_name=tools[index % len(tools)], args=args, result="ok", task_id="bench")
        elapsed = time.perf_counter() - started
        return elapsed * 1000.0 / calls, ledger_bytes(plugin)
    finally:
        if old is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old


def breakdown(arg_bytes: int = 200, calls: int = 300) -> None:
    """Time each step of the current hook, so a change can be attributed to a step."""
    home = pathlib.Path(tempfile.mkdtemp(prefix="zkbench-"))
    os.environ["HERMES_HOME"] = str(home)
    plugin = load_plugin()
    args = make_args(arg_bytes)
    for index in range(20):
        plugin._record("read_file", make_args(arg_bytes, index))

    def bench(fn, n=calls):
        fn()
        gc.collect()
        start = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - start) * 1e6 / n

    def has(name: str) -> bool:
        return callable(getattr(plugin, name, None))

    steps: list[tuple[str, float]] = [("_record (whole hook path)", bench(lambda: plugin._record("read_file", args), 200))]
    steps.append(("_digest (sha256 of canonical args)", bench(lambda: plugin._digest(args))))
    steps.append(("_costs", bench(lambda: plugin._costs())))
    steps.append(("_load_state", bench(lambda: plugin._load_state())))
    for name in ("_signature", "_row_line", "_append_row", "_pending"):
        if has(name):
            if name == "_signature":
                fn = lambda: plugin._signature(plugin._state_path())
            elif name == "_row_line":
                row = {"tool": "read_file", "target": 1, "amount": 1, "trace_digest": 1, "at": 1}
                fn = lambda: plugin._row_line(row)
            elif name == "_append_row":
                row = {"tool": "read_file", "target": 1, "amount": 1, "trace_digest": 1, "at": 1}
                fn = lambda: plugin._append_row(row)
            else:
                fn = lambda: plugin._pending()
            steps.append((name, bench(fn)))
    width = max(len(label) for label, _ in steps) + 2
    for label, us in steps:
        print(f"{label:<{width}} {us:8.2f} us")
    print(f"{'ledger on disk':<{width}} {ledger_bytes(plugin):8d} B")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=200)
    parser.add_argument("--arg-bytes", type=int, default=None)
    parser.add_argument("--repeat", type=int, default=1, help="runs per configuration; prints the median")
    parser.add_argument("--breakdown", action="store_true")
    args = parser.parse_args()

    if args.breakdown:
        breakdown(args.arg_bytes or 200)
        return 0

    for size in [args.arg_bytes] if args.arg_bytes else [200, 200 * 1024]:
        samples = [time_hook(args.calls, size) for _ in range(args.repeat)]
        ms = statistics.median(sample[0] for sample in samples)
        kib = samples[0][1] / 1024.0
        print(f"args {size:>7} B   {ms:8.3f} ms/call   on disk after {args.calls} calls: {kib:6.1f} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
