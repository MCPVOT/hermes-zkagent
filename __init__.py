"""zkagent-attest — verifiable receipts for what an agent actually did.

The plugin records every tool call the agent makes (a `post_tool_call` hook), then turns
the recorded session into a zkagent attestation: a signed policy sets the caps, a Groth16
proof shows the session stayed inside them, and the receipt publishes totals only. The
actions themselves never leave the witness; `zkagent_disclose` reveals exactly one.

What it is not: it does not meter real money, it does not sandbox the agent, and it does
not prove the tool *result* was honest. It proves that the recorded set of actions
explains the published totals inside the signed policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

PLUGIN_NAME = "zkagent-attest"
TOOLSET = "zkagent"
MAX_ACTIONS = 8  # zkagent-core::MAX_ACTIONS: the circuit's fixed action slots
ZERO_ROOT = "0" * 64

# Relative cost of a tool call in policy units. Deliberately small and visible: the
# point is to make the cap checkable, not to price real work accurately.
DEFAULT_COSTS = {
    "read_file": 1,
    "write_file": 2,
    "patch": 2,
    "terminal": 3,
    "web_search": 2,
    "web_extract": 3,
    "browser_exec": 3,
}

# --------------------------------------------------------------------------- state

# The ledger is two files. `state.json` is the session header — cost table, target map, run
# history — and is written only when one of those changes. `pending.jsonl` is one compact
# JSON row per tool call, appended with a single O_APPEND write. `post_tool_call` runs on
# every tool call the agent makes, so its path is "append a line", not "read 28 KB, modify,
# rewrite 28 KB". Deleting state.json still means "start a fresh session": a ledger without
# a header is not a ledger, and the next recorded call truncates it.

PENDING_FILE = "pending.jsonl"
_UNSET = object()

# Cost table and target map, rebuilt from state.json only when its signature changes. A cache
# and nothing more: a reloaded module, or a second session on the same profile, rebuilds it
# from disk, so nothing here can go missing when the process goes away.
_PARAMS: dict[str, Any] = {"sig": _UNSET, "costs": dict(DEFAULT_COSTS), "targets": {}}


def _resolve_data_dir() -> Path:
    """The plugin's own persistent directory (survives updates and removal)."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        return plugin_data_dir(PLUGIN_NAME)
    except Exception:
        home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
        root = home / "plugin-data" / PLUGIN_NAME
        root.mkdir(parents=True, exist_ok=True)
        return root


_DATA_DIR: Path | None = None


def _data_dir() -> Path:
    """Resolved once per process: the host resolves a profile at startup and does not move it.

    Resolving it per call is not free — the `plugins.plugin_storage` import raises and is
    caught on every path, which costs more than the rest of the hook.
    """
    global _DATA_DIR
    if _DATA_DIR is None:
        _DATA_DIR = _resolve_data_dir()
    return _DATA_DIR


def _state_path() -> Path:
    return _data_dir() / "state.json"


def _pending_path() -> Path:
    return _data_dir() / PENDING_FILE


def _signature(path: Path) -> tuple[int, int, int] | None:
    """Cheap "did this change" token: mtime, size and inode (an atomic replace is a new inode)."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


def _load_state() -> dict:
    path = _state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("runs", [])
    state.setdefault("next_run", 1)
    state.setdefault("next_run_id", 1001)
    if "pending" in state:
        _migrate_pending(state)
    return state


def _save_state(state: dict) -> None:
    path = _state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    _cache_params(state)


def _costs_and_targets(state: Mapping[str, Any]) -> tuple[dict, dict]:
    costs = dict(DEFAULT_COSTS)
    costs.update(state.get("costs") or {})
    return costs, dict(state.get("targets") or {})


def _cache_params(state: Mapping[str, Any]) -> None:
    costs, targets = _costs_and_targets(state)
    _PARAMS["sig"] = _signature(_state_path())
    _PARAMS["costs"] = costs
    _PARAMS["targets"] = targets


def _params(create: bool = False) -> tuple[dict, dict]:
    """Cost table and target map, re-read only when state.json changes on disk.

    `create` is the hook's call: recording a call with no state.json starts the session, the
    same reset the README documents as "clear state.json".
    """
    sig = _signature(_state_path())
    if sig is not None and _PARAMS["sig"] == sig:
        return _PARAMS["costs"], _PARAMS["targets"]
    state = _load_state()
    if sig is None and create:
        _start_session(state)
        return _PARAMS["costs"], _PARAMS["targets"]
    _cache_params(state)
    return _PARAMS["costs"], _PARAMS["targets"]


def _costs() -> dict:
    """Cost table: the state file wins, so a user can edit it without reinstalling."""
    return _params()[0]


# ------------------------------------------------------------------------- binary


def _binary() -> str | None:
    env = (os.environ.get("ZKAGENT_BIN") or "").strip()
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    found = shutil.which("zkagent")
    return found or None


def _run(binary: str, args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    proc = subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _version(binary: str) -> dict:
    code, out, _ = _run(binary, ["version"], timeout=30)
    if code != 0:
        return {"ok": False}
    parts: dict[str, Any] = {"ok": True, "raw": out.strip()}
    for token in out.split():
        if "=" in token:
            key, _, value = token.partition("=")
            parts[key.strip()] = value.strip()
    return parts


# -------------------------------------------------------------------------- ledger


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(args: Mapping[str, Any]) -> int:
    """A 64-bit commitment to the arguments. The arguments themselves are never stored.

    zkagent's leaf is the digest alone, so this value is what a disclosure reveals. Keeping
    it a one-way digest of the call is what makes "reveal one action" safe: it names the
    call without republishing its payload.
    """
    raw = hashlib.sha256(_canonical(args).encode("utf-8")).digest()
    return int.from_bytes(raw[:8], "big") & ((1 << 63) - 1)


def _row_line(row: Mapping[str, Any]) -> bytes:
    return (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read_rows() -> list[dict]:
    """Parse the ledger, dropping a torn final line (a process killed mid-write)."""
    try:
        text = _pending_path().read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _pending(state: Mapping[str, Any] | None = None) -> list[dict]:
    """The pending actions. No state.json means no session, so a stale log is not a ledger.

    Passing the `state` the caller already loaded skips a second read; the standalone call
    exists so a 0.1.0 ledger (rows inline in state.json) is migrated before it is read.
    """
    if not _state_path().exists():
        return []
    if state is None:
        _load_state()
    return _read_rows()


def _write_ledger(rows: list[dict]) -> None:
    path = _pending_path()
    if not rows:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        for row in rows:
            handle.write(_row_line(row))
    os.replace(tmp, path)


def _append_row(row: Mapping[str, Any]) -> None:
    """One append: no read, no rewrite. O_APPEND makes the row land atomically."""
    fd = os.open(_pending_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, _row_line(row))
    finally:
        os.close(fd)


def _start_session(state: dict) -> None:
    """A recorded call with no state.json begins a session: drop the old log, write the header."""
    _write_ledger([])
    _save_state(state)


def _compact(consumed: int) -> list[dict]:
    """Drop the rows a receipt consumed; keep anything appended while it was being proven."""
    remaining = _read_rows()[consumed:]
    _write_ledger(remaining)
    return remaining


def _migrate_pending(state: dict) -> None:
    """0.1.0 kept the ledger inside state.json; move those rows to the append-only file once."""
    legacy = state.get("pending")
    rows = [row for row in legacy if isinstance(row, dict)] if isinstance(legacy, list) else []
    existing = _read_rows()
    try:
        _write_ledger(rows + existing)
    except OSError:
        return
    state.pop("pending", None)
    try:
        _save_state(state)
    except OSError:
        state["pending"] = rows  # state.json stays authoritative; do not duplicate the rows
        try:
            _write_ledger(existing)
        except OSError:
            pass


def _target_id(tool_name: str, targets: dict) -> int:
    """The tool's slot in the policy allow-list; a new tool takes the next id, once."""
    target = targets.get(tool_name)
    if target is not None:
        return int(target)
    state = _load_state()
    current = state.setdefault("targets", {})
    if tool_name not in current:
        current[tool_name] = len(current) + 1
        _save_state(state)
    return int(current[tool_name])


def _record(tool_name: str, args: Mapping[str, Any]) -> None:
    """Append one action to the pending log. Never raises: a hook must not break a call."""
    if not tool_name or tool_name.startswith("zkagent_"):
        return
    costs, targets = _params(create=True)
    _append_row(
        {
            "tool": tool_name,
            "target": _target_id(tool_name, targets),
            "amount": int(costs.get(tool_name, 1)),
            "trace_digest": _digest(args),
            "at": int(time.time()),
        }
    )


def _on_post_tool_call(tool_name: str, args: Any = None, result: Any = None, task_id: Any = None, **_: Any) -> None:
    try:
        _record(str(tool_name or ""), args if isinstance(args, Mapping) else {"args": args})
    except Exception:
        return


# -------------------------------------------------------------------------- tools


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _need_binary() -> tuple[str | None, str | None]:
    binary = _binary()
    if binary:
        return binary, None
    return None, (
        "zkagent binary not found. Install the zkagent release (or build the workspace) "
        "and either put `zkagent` on PATH or set ZKAGENT_BIN to its path."
    )


def tool_status(args: Mapping[str, Any] | None = None) -> str:
    binary, error = _need_binary()
    state = _load_state()
    pending = _pending(state)
    runs = state.get("runs") or []
    payload: dict[str, Any] = {
        "plugin": PLUGIN_NAME,
        "binary": binary,
        "pending_actions": len(pending),
        "slots_per_receipt": MAX_ACTIONS,
        "runs": len(runs),
        "last_run": runs[-1] if runs else None,
    }
    if error:
        payload["error"] = error
    elif binary:
        payload["zkagent"] = _version(binary)
    return _json(payload)


def _keys_dir() -> Path:
    return _data_dir() / "keys"


def _authority_path() -> Path:
    return _data_dir() / "authority.hex"


def _ensure_setup(binary: str) -> None:
    keys = _keys_dir()
    if (keys / "vk.bin").is_file() and (keys / "pk.bin").is_file():
        return
    keys.mkdir(parents=True, exist_ok=True)
    seed = str(int.from_bytes(os.urandom(8), "big") >> 1)
    code, out, err = _run(binary, ["setup", "--seed", seed, "--out", str(keys)], timeout=300)
    if code != 0:
        raise RuntimeError(f"zkagent setup failed ({code}): {(err or out).strip()[:400]}")
    os.chmod(keys, 0o700)


def tool_attest(args: Mapping[str, Any] | None = None) -> str:
    params = dict(args or {})
    binary, error = _need_binary()
    if error:
        return _json({"ok": False, "error": error})

    state = _load_state()
    pending = _pending(state)
    if not pending:
        return _json({"ok": False, "error": "nothing to attest yet: no tool calls recorded, and the session is empty."})

    batch = pending[:MAX_ACTIONS]
    total = sum(int(a["amount"]) for a in batch)
    count = len(batch)

    max_amount = int(params.get("max_amount", total))
    max_per_action = int(params.get("max_per_action", max(int(a["amount"]) for a in batch)))
    ttl = int(params.get("ttl_seconds", 3600))
    chain_id = int(params.get("chain_id", 1))
    run_id = int(params.get("execution_id", state.get("next_run_id", 1001)))

    if total > max_amount:
        return _json(
            {
                "ok": False,
                "error": f"the session total ({total}) exceeds the cap you asked to prove ({max_amount}); "
                         "a proof of a false statement is worse than no proof. Raise max_amount or attest fewer actions.",
                "total": total,
                "cap": max_amount,
            }
        )
    if any(int(a["amount"]) > max_per_action for a in batch):
        return _json({"ok": False, "error": f"an action costs more than max_per_action ({max_per_action})"})

    run_dir = _data_dir() / "runs" / f"run-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    _ensure_setup(binary)

    policy_path = run_dir / "policy.json"
    receipt_path = run_dir / "receipt.json"
    actions_path = run_dir / "actions.json"
    proof_path = run_dir / "proof.json"

    targets = sorted({int(a["target"]) for a in batch})
    code, out, err = _run(
        binary,
        [
            "policy",
            "--max-amount", str(max_amount),
            "--max-per-action", str(max_per_action),
            "--expires-at", str(int(time.time()) + ttl),
            "--chain-id", str(chain_id),
            "--targets", ",".join(str(t) for t in targets),
            "--authority-key", str(_authority_path()),
            "--out", str(policy_path),
        ],
    )
    if code != 0:
        return _json({"ok": False, "stage": "policy", "error": (err or out).strip()[:400]})

    receipt_path.write_text(
        json.dumps(
            {
                "total_amount": total,
                "action_count": count,
                "completed_at": int(time.time()),
                "chain_id": chain_id,
                "execution_id": run_id,
                "trace_root": ZERO_ROOT,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    actions_path.write_text(
        json.dumps(
            [
                {"target": int(a["target"]), "amount": int(a["amount"]), "trace_digest": int(a["trace_digest"])}
                for a in batch
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    code, out, err = _run(
        binary,
        [
            "--json", "prove",
            "--keys", str(_keys_dir()),
            "--policy", str(policy_path),
            "--receipt", str(receipt_path),
            "--actions", str(actions_path),
            "--out", str(proof_path),
        ],
        timeout=300,
    )
    if code != 0:
        return _json({"ok": False, "stage": "prove", "error": (err or out).strip()[:400]})

    code, out, err = _run(
        binary,
        [
            "--json", "verify",
            "--keys", str(_keys_dir()),
            "--policy", str(policy_path),
            "--receipt", str(receipt_path),
            "--proof", str(proof_path),
            "--now", str(int(time.time())),
        ],
    )
    verdict: dict[str, Any] = {"exit": code}
    try:
        verdict.update(json.loads(out.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        verdict["raw"] = (out or err).strip()[:400]

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    run = {
        "run_id": run_id,
        "at": int(time.time()),
        "actions": count,
        "total": total,
        "cap": max_amount,
        "verdict": verdict.get("verdict") or verdict.get("raw"),
        "reason": verdict.get("reason"),
        "dir": str(run_dir),
        "trace_root": receipt.get("trace_root"),
        "tools": [a["tool"] for a in batch],
    }
    state["runs"] = state.get("runs") or []
    state["runs"].append(run)
    state["next_run_id"] = run_id + 1
    # Consume the rows before recording the run: a failure here leaves the ledger untouched and
    # the attestation retryable, instead of a receipt whose actions are still pending.
    remaining = _compact(count)
    _save_state(state)

    return _json(
        {
            "ok": code == 0,
            "verdict": verdict.get("verdict"),
            "reason": verdict.get("reason"),
            "execution_id": run_id,
            "actions": count,
            "total": total,
            "cap": max_amount,
            "pending_after": len(remaining),
            "receipt": str(receipt_path),
            "proof": str(proof_path),
            "policy": str(policy_path),
        }
    )


def tool_verify(args: Mapping[str, Any] | None = None) -> str:
    params = dict(args or {})
    binary, error = _need_binary()
    if error:
        return _json({"ok": False, "error": error})
    state = _load_state()
    runs = state.get("runs") or []
    if not runs:
        return _json({"ok": False, "error": "no attestation yet: call zkagent_attest first."})
    run_id = int(params.get("run_id", runs[-1]["run_id"]))
    run = next((r for r in runs if int(r["run_id"]) == run_id), None)
    if not run:
        return _json({"ok": False, "error": f"no attestation with execution_id {run_id}"})

    run_dir = Path(run["dir"])
    cmd = [
        "--json", "verify",
        "--keys", str(_keys_dir()),
        "--policy", str(run_dir / "policy.json"),
        "--receipt", str(run_dir / "receipt.json"),
        "--proof", str(run_dir / "proof.json"),
    ]
    now = params.get("now")
    if now is not None:
        cmd += ["--now", str(int(now))]
    code, out, err = _run(binary, cmd)
    verdict: dict[str, Any] = {"ok": code == 0, "exit": code}
    try:
        verdict.update(json.loads(out.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        verdict["raw"] = (out or err).strip()[:400]
    verdict["execution_id"] = run_id
    return _json(verdict)


def tool_disclose(args: Mapping[str, Any] | None = None) -> str:
    params = dict(args or {})
    binary, error = _need_binary()
    if error:
        return _json({"ok": False, "error": error})
    state = _load_state()
    runs = state.get("runs") or []
    if not runs:
        return _json({"ok": False, "error": "no attestation yet: call zkagent_attest first."})
    run = runs[-1]
    index = int(params.get("index", 0))
    if index < 0 or index >= int(run["actions"]):
        return _json({"ok": False, "error": f"index {index} is outside this receipt's {run['actions']} actions"})

    out_path = Path(run["dir"]) / f"disclosure-{index}.json"
    code, out, err = _run(
        binary,
        [
            "--json", "disclose",
            "--actions", str(Path(run["dir"]) / "actions.json"),
            "--index", str(index),
            "--receipt", str(Path(run["dir"]) / "receipt.json"),
            "--out", str(out_path),
        ],
    )
    payload: dict[str, Any] = {"ok": code == 0, "exit": code, "index": index, "out": str(out_path)}
    try:
        payload.update(json.loads(out.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        payload["raw"] = (out or err).strip()[:400]
    tools = run.get("tools") or []
    if 0 <= index < len(tools):
        payload["recorded_tool"] = tools[index]
    return _json(payload)


def _schema(name: str, description: str, properties: Mapping[str, Any], required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": dict(properties), "required": list(required or [])},
    }


def register(ctx: Any) -> None:
    """Wire the tools and the ledger hook into the host."""
    ctx.register_tool(
        name="zkagent_status",
        toolset=TOOLSET,
        schema=_schema(
            "zkagent_status",
            "Report the zkagent binary, the pending action count and the last attestation.",
            {},
        ),
        handler=tool_status,
    )
    ctx.register_tool(
        name="zkagent_attest",
        toolset=TOOLSET,
        schema=_schema(
            "zkagent_attest",
            "Attest the session: sign a policy, prove the recorded actions stayed inside it, and return the verdict. "
            "Publishes totals only; the actions stay private.",
            {
                "max_amount": {"type": "integer", "description": "Total cap to prove against (defaults to the session total)."},
                "max_per_action": {"type": "integer", "description": "Per-action cap (defaults to the most expensive action)."},
                "ttl_seconds": {"type": "integer", "description": "Policy lifetime in seconds (default 3600)."},
                "chain_id": {"type": "integer", "description": "Chain id recorded in the receipt (default 1)."},
            },
        ),
        handler=tool_attest,
    )
    ctx.register_tool(
        name="zkagent_verify",
        toolset=TOOLSET,
        schema=_schema(
            "zkagent_verify",
            "Re-verify the latest (or a named) attestation and return its verdict.",
            {
                "run_id": {"type": "integer", "description": "Execution id to verify; defaults to the newest run."},
                "now": {"type": "integer", "description": "Unix time for the future-dating check."},
            },
        ),
        handler=tool_verify,
    )
    ctx.register_tool(
        name="zkagent_disclose",
        toolset=TOOLSET,
        schema=_schema(
            "zkagent_disclose",
            "Reveal one action's digest plus its inclusion path, leaving the other actions hidden.",
            {"index": {"type": "integer", "description": "0-based action slot to disclose."}},
            required=["index"],
        ),
        handler=tool_disclose,
    )
    ctx.register_hook("post_tool_call", _on_post_tool_call)
