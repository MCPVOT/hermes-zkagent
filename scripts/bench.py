#!/usr/bin/env python3
"""Measure the cost of the post_tool_call hook, the way a user feels it.

    python3 scripts/bench.py                 # 200-byte and 200 KB args, 200 calls
    python3 scripts/bench.py --calls 500 --arg-bytes 200
    python3 scripts/bench.py --breakdown     # per-component cost inside the hook

The hook runs synchronously on every tool call the agent makes, so the number that matters
is wall time per call, not lines of code. State is written to a throwaway HERMES_HOME so a
run never touches the ledger a real session is using.
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


def time_hook(module, calls: int, arg_bytes: int, fresh: bool = True) -> tuple[float, int, pathlib.Path]:
    home = pathlib.Path(tempfile.mkdtemp(prefix="zkbench-"))
    old = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    try:
        plugin = load_plugin()
        state = plugin._state_path()
        if fresh and state.exists():
            state.unlink()
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
        size = state.stat().st_size if state.exists() else 0
        return elapsed * 1000.0 / calls, size, state
    finally:
        if old is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old


def breakdown(module_path=None, calls: int = 200) -> None:
    """Time each step the hook performs, on the unmodified state shape."""
    home = pathlib.Path(tempfile.mkdtemp(prefix="zkbench-"))
    os.environ["HERMES_HOME"] = str(home)
    plugin = load_plugin()
    state = plugin._state_path()
    for index in range(200):
        plugin._record("read_file", make_args(200, index))
    body = json.loads(state.read_text())
    text = state.read_text()
    args = make_args(200)

    def bench(fn, n=calls):
        fn()
        gc.collect()
        start = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - start) * 1e6 / n

    load_us = bench(lambda: json.loads(text))
    dump_us = bench(lambda: json.dumps(body, indent=2, sort_keys=True))
    canonical_us = bench(lambda: plugin._canonical(args))
    digest_us = bench(lambda: plugin._digest(args))
    costs_us = bench(lambda: plugin._costs())
    read_us = bench(lambda: state.read_text(encoding="utf-8"))
    tmp = state.with_suffix(".tmp")
    dump_text = json.dumps(body, indent=2, sort_keys=True)

    def write_only():
        tmp.write_text(dump_text, encoding="utf-8")

    def write_replace():
        tmp.write_text(dump_text, encoding="utf-8")
        os.replace(tmp, state)

    write_us = bench(write_only)
    replace_us = bench(write_replace) - write_us
    print(f"state bytes                 {len(text):>10,}")
    print(f"read_text                   {read_us:>10.1f} us")
    print(f"json.loads                  {load_us:>10.1f} us")
    print(f"json.dumps(indent=2)        {dump_us:>10.1f} us")
    print(f"write_text                  {write_us:>10.1f} us")
    print(f"os.replace                  {replace_us:>10.1f} us")
    print(f"_canonical(args)            {canonical_us:>10.1f} us")
    print(f"_digest(args)               {digest_us:>10.1f} us")
    print(f"_costs() [loads state]      {costs_us:>10.1f} us")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=200)
    parser.add_argument("--arg-bytes", type=int, default=None)
    parser.add_argument("--repeat", type=int, default=1, help="runs per configuration; prints the median")
    parser.add_argument("--breakdown", action="store_true")
    args = parser.parse_args()

    if args.breakdown:
        breakdown()
        return 0

    sizes = [args.arg_bytes] if args.arg_bytes else [200, 200 * 1024]
    for size in sizes:
        samples = [time_hook(load_plugin(), args.calls, size) for _ in range(args.repeat)]
        ms = statistics.median(sample[0] for sample in samples)
        kib = samples[-1][1] / 1024.0
        print(f"args {size:>7} B   {ms:8.3f} ms/call   state after {args.calls} calls: {kib:6.1f} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
