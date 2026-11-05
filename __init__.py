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

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

PLUGIN_NAME = "zkagent-attest"
TOOLSET = "zkagent"
MAX_ACTIONS = 8  # zkagent-core::MAX_ACTIONS: the circuit's fixed action slots
ZERO_ROOT = "0" * 64

# Relative cost of a tool call in policy units. Deliberately small and visible: the
# point is to make the cap checkable, not to price real work accurately. These values are
# also `plugin.yaml`'s `config_schema` defaults; the two must stay identical (a host that
# renders the settings form and a plugin that falls back to this table would otherwise
# quote different prices for the same call).
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
SESSION_FILE = "session.json"
SEEN_FILE = "seen.json"
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


def _session_path() -> Path:
    return _data_dir() / SESSION_FILE


def _seen_path() -> Path:
    return _data_dir() / SEEN_FILE


# The host's `register(ctx)` handle, kept so the cost table can be read from the host's own
# settings (`plugins.entries.zkagent-attest.settings.<tool>`). `ctx.get_config` is the only
# supported reader; there is no module-level API for it, so the ctx is stashed at register.
_CTX: Any = None
_HOST_COSTS: dict[str, int] | None | object = _UNSET


def _read_host_costs() -> dict[str, int] | None:
    """The cost table from the host config, or None when the host exposes no such setting.

    Two ways in, in order:
      1. `ctx.get_config(key)` on the context `register()` received — the documented API;
      2. a direct read of `plugins.entries.<id>.settings` via `hermes_cli`, for a reloaded
         module whose `register()` ran before the ctx was stored (or a test harness that
         never registers).
    Both are best-effort. Honest fallback: when neither yields a value (Hermes without the
    settings API, a hand-run module, a profile with no entry for this plugin), the caller
    keeps state.json's `costs` and then DEFAULT_COSTS. Nothing here raises: a broken host
    config must not stop the ledger from recording.
    """
    found: dict[str, Any] = {}
    ctx = _CTX
    if ctx is not None:
        for key in DEFAULT_COSTS:
            try:
                value = ctx.get_config(key, None)
            except Exception:
                value = None
            if value is not None:
                found[key] = value
    if not found:
        try:
            from hermes_cli.config import load_config_readonly
            from hermes_cli.plugins_state import _plugin_settings_entry

            entry = _plugin_settings_entry(load_config_readonly() or {}, PLUGIN_NAME) or {}
            settings = entry.get("settings")
            if isinstance(settings, Mapping):
                keys = set(DEFAULT_COSTS) | {str(k) for k in settings}
                for key in keys:
                    if key in settings:
                        found[key] = settings[key]
        except Exception:
            pass
    clean: dict[str, int] = {}
    for key, value in found.items():
        try:
            price = int(value)
        except (TypeError, ValueError):
            continue  # a non-numeric setting is ignored, not fatal
        if price >= 0:
            clean[str(key)] = price
    return clean or None


def _host_costs() -> dict[str, int] | None:
    """Read the host settings once per process: the cost table changes between sessions, not
    between two tool calls, and the hook's path must stay a dict lookup."""
    global _HOST_COSTS
    if _HOST_COSTS is _UNSET:
        _HOST_COSTS = _read_host_costs()
    return _HOST_COSTS  # type: ignore[return-value]


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
    """Cost table precedence: DEFAULT_COSTS < state.json `costs` < host settings.

    The host config wins per key because it is the surface the Desktop settings form writes;
    state.json's `costs` still applies for keys the host does not set, and on a host that
    exposes no setting at all it is the whole table. See `_read_host_costs` for why the host
    read can legitimately return nothing.
    """
    costs = dict(DEFAULT_COSTS)
    costs.update(state.get("costs") or {})
    host = _host_costs() or {}
    costs.update(host)
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


# ---------------------------------------------------------------- binary and install
#
# `scripts/install-zkagent.py` is the repository's checksum-pinned fetch of the upstream
# release. It is the only installer this plugin will run: importing it and calling `main()`
# keeps the sha256 check, the pinned version and the asset table in one place, so an
# auto-fetch can never be a weaker install than the documented one. Nothing is downloaded
# unless `_binary()` found nothing at all, and the attempt is reported to the caller.

INSTALL_SCRIPT = Path(__file__).resolve().parent / "scripts" / "install-zkagent.py"
INSTALL_DIR = Path.home() / ".local" / "bin"
_INSTALL_ATTEMPTED = False
_INSTALLED_BIN: str | None = None


def _executable(path: str | None) -> str | None:
    if path and os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    return None


def _install_binary() -> dict:
    """Run the pinned installer into INSTALL_DIR, once per process, and say exactly what happened.

    Returns a report; `ok` is True only when the installer exited 0. The output is captured
    (the installer prints the URL, the digest and the destination) and echoed to the tool
    result, because installing a binary the user did not ask for and not saying so would be
    the wrong kind of convenience. It is not run when `ZKAGENT_AUTOINSTALL=0`.
    """
    global _INSTALL_ATTEMPTED, _INSTALLED_BIN
    report: dict[str, Any] = {
        "attempted": True,
        "dir": str(INSTALL_DIR),
        "script": str(INSTALL_SCRIPT),
        "checksum": "pinned sha256, verified by scripts/install-zkagent.py",
    }
    if (os.environ.get("ZKAGENT_AUTOINSTALL") or "").strip().lower() in {"0", "false", "no"}:
        return {**report, "attempted": False, "ok": False, "skipped": "ZKAGENT_AUTOINSTALL=0"}
    if _INSTALL_ATTEMPTED:
        return {**report, "attempted": False, "ok": False, "skipped": "already attempted in this process"}
    _INSTALL_ATTEMPTED = True
    if not INSTALL_SCRIPT.is_file():
        return {**report, "ok": False, "error": f"installer not found at {INSTALL_SCRIPT}"}

    buffer = io.StringIO()
    saved_argv = sys.argv
    try:
        spec = importlib.util.spec_from_file_location("zkagent_installer", INSTALL_SCRIPT)
        if spec is None or spec.loader is None:
            return {**report, "ok": False, "error": "cannot load the installer"}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.argv = ["install-zkagent.py", "--dir", str(INSTALL_DIR)]
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = int(module.main() or 0)
    except SystemExit as exc:  # unsupported platform or a usage error: the installer says why
        code = int(exc.code or 1) if isinstance(exc.code, int) else 1
    except Exception as exc:  # no network, no disk, a changed release: report, do not raise
        code = 1
        buffer.write(f"{type(exc).__name__}: {exc}")
    finally:
        sys.argv = saved_argv

    output = buffer.getvalue().strip()
    report["ok"] = code == 0
    report["exit"] = code
    report["output"] = output[-1200:]
    if code == 0:
        target = INSTALL_DIR / ("zkagent.exe" if os.name == "nt" else "zkagent")
        _INSTALLED_BIN = _executable(str(target))
        report["installed"] = str(target)
    return report


def _resolve_binary() -> tuple[str | None, dict | None]:
    """Locate zkagent: ZKAGENT_BIN, then PATH, then (once) the pinned installer.

    The third step is what makes "install the plugin and it works" true, and the report it
    returns is the receipt for that step. `zkagent_status` shows it; the other tools attach
    it when it fired during their own call.
    """
    env = _executable((os.environ.get("ZKAGENT_BIN") or "").strip())
    if env:
        return env, None
    found = shutil.which("zkagent")
    if found:
        return found, None
    if _INSTALLED_BIN:
        return _INSTALLED_BIN, None
    report = _install_binary()
    binary = _INSTALLED_BIN or _executable(str(INSTALL_DIR / "zkagent")) or shutil.which("zkagent")
    return (binary or None), report


def _binary() -> str | None:
    return _resolve_binary()[0]


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
    """The first recorded call of a session: claim the header, then start a clean log.

    The header is claimed with O_EXCL, so two processes starting at the same moment cannot
    each truncate the other's rows: the loser re-reads the winner's header and appends to
    the log that already exists.
    """
    try:
        os.close(os.open(_state_path(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        _cache_params(_load_state())
        return
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


# ------------------------------------------------------------------ session index
#
# The circuit has 8 action slots, so a real session produces several receipts. Each receipt
# is independently verifiable; what is missing between them is the claim "these are the
# receipts, in this order, and together they are the session". `session.json` is that index,
# written beside `runs/` after every attestation.
#
# It carries no signature of its own, and needs none: every number in it is copied from a
# receipt on disk or recomputed from those receipts. A tampered index is therefore caught by
# comparing it with the artifacts — the listed trace roots must equal the receipt files, the
# listed totals must equal their `total_amount`, the sum must equal the session total, and
# the stored root must equal the root recomputed from the listed order. Any disagreement is
# a hard failure in `zkagent_verify`, never a warning: an index that disagrees with the
# receipts is either corrupt or a forgery, and both mean "do not accept".
#
# Merkle construction (deliberately small enough to re-implement from this paragraph):
#   leaf      = sha256(trace_root bytes)      — the receipt's 32-byte root, decoded from hex
#   node      = sha256(left || right)         — the two children's 32-byte digests, in order
#   odd node  = promoted unchanged            — no duplication, no padding leaf
#   one leaf  = that leaf's hex               — the root of a single-receipt session
#   no leaves = ZERO_ROOT (64 hex zeros)      — a defined root for an empty index
# Order is part of the commitment: leaves are taken in ascending execution_id order, and the
# list is checked to be in that order, so reordering the receipts changes the root.


def _merkle_root(roots: Sequence[str]) -> str:
    """sha256 binary tree over the ordered receipt trace roots (see the construction above)."""
    if not roots:
        return ZERO_ROOT
    level = [hashlib.sha256(bytes.fromhex(str(root))).digest() for root in roots]
    while len(level) > 1:
        paired = [
            hashlib.sha256(level[i] + level[i + 1]).digest() for i in range(0, len(level) - 1, 2)
        ]
        if len(level) % 2:
            paired.append(level[-1])
        level = paired
    return level[0].hex()


def _relative(path: Path) -> str:
    """Path as recorded in session.json: relative to the plugin data dir, so the index travels."""
    try:
        return str(path.resolve().relative_to(_data_dir().resolve()))
    except (ValueError, OSError):
        return str(path)


def _receipt_entry(run: Mapping[str, Any]) -> dict:
    """One index row, read from the receipt file rather than from state.json's summary.

    The receipt on disk is the evidence; the run history is the plugin's note about it. If
    the two disagree, the file wins and the discrepancy surfaces as a verify failure.
    """
    run_dir = Path(str(run.get("dir") or ""))
    receipt_path = run_dir / "receipt.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"cannot index execution_id {run.get('run_id')}: {receipt_path} is unreadable ({exc})"
        ) from exc
    root = str(receipt.get("trace_root") or "")
    try:
        if len(root) != 64:
            raise ValueError("not a 32-byte hex root")
        bytes.fromhex(root)
    except ValueError as exc:
        raise RuntimeError(f"receipt {receipt_path} has an unusable trace_root ({exc})") from exc
    return {
        "execution_id": int(receipt.get("execution_id", run.get("run_id"))),
        "trace_root": root,
        "total": int(receipt.get("total_amount", 0)),
        "actions": int(receipt.get("action_count", 0)),
        "receipt": _relative(receipt_path),
    }


def _build_session(runs: Sequence[Mapping[str, Any]]) -> dict:
    entries = sorted((_receipt_entry(run) for run in runs), key=lambda e: e["execution_id"])
    roots = [entry["trace_root"] for entry in entries]
    return {
        "version": 1,
        "updated_at": int(time.time()),
        "receipt_count": len(entries),
        "action_count": sum(entry["actions"] for entry in entries),
        "total": sum(entry["total"] for entry in entries),
        "root": _merkle_root(roots),
        "merkle": {
            "hash": "sha256",
            "leaf": "sha256(trace_root bytes)",
            "node": "sha256(left || right)",
            "odd": "promoted unchanged",
            "empty": ZERO_ROOT,
            "order": "ascending execution_id",
            "leaf_count": len(roots),
        },
        "receipts": entries,
    }


def _write_session(runs: Sequence[Mapping[str, Any]]) -> dict:
    """Rebuild the index from the run history and replace it atomically.

    Rebuilt, not appended: the receipts on disk are the source, so a lost or edited
    `session.json` is repaired by the next attestation instead of drifting further.
    """
    index = _build_session(runs)
    path = _session_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return index


def _session_report() -> dict:
    """Cross-check session.json against the receipts on disk. Never raises; `ok` is the verdict.

    `entries` is the internal, resolved form (with absolute run dirs); everything else is
    safe to publish in a tool result.
    """
    path = _session_path()
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "present": False,
            "ok": False,
            "error": f"no {SESSION_FILE} beside the runs; run zkagent_attest to build the session index",
        }
    except (OSError, ValueError) as exc:
        return {"present": True, "ok": False, "problems": [f"{SESSION_FILE} is unreadable: {exc}"]}
    if not isinstance(index, Mapping) or not isinstance(index.get("receipts"), list):
        return {"present": True, "ok": False, "problems": [f"{SESSION_FILE} has no receipts list"]}

    problems: list[str] = []
    entries: list[dict] = []
    roots: list[str] = []
    data_root = _data_dir().resolve()
    previous_id: int | None = None
    for position, raw in enumerate(index.get("receipts") or []):
        if not isinstance(raw, Mapping):
            problems.append(f"receipts[{position}] is not an object")
            continue
        try:
            execution_id = int(raw.get("execution_id"))
            listed_total = int(raw.get("total"))
            listed_actions = int(raw.get("actions"))
            root = str(raw.get("trace_root") or "")
        except (TypeError, ValueError):
            problems.append(f"receipts[{position}] has non-numeric execution_id/total/actions")
            continue
        if previous_id is not None and execution_id <= previous_id:
            problems.append(f"receipts[{position}] (execution_id {execution_id}) is out of order")
        previous_id = execution_id
        receipt_rel = str(raw.get("receipt") or "")
        receipt_path = (data_root / receipt_rel).resolve()
        if not str(receipt_path).startswith(str(data_root) + os.sep):
            problems.append(f"receipts[{position}] points outside the data dir: {receipt_rel!r}")
            continue
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"receipt for execution_id {execution_id} is missing or unreadable ({receipt_rel}: {exc})")
            continue
        if str(receipt.get("trace_root") or "") != root:
            problems.append(
                f"receipts[{position}] lists trace_root {root[:16]}… but {receipt_rel} says "
                f"{str(receipt.get('trace_root'))[:16]}…"
            )
        if int(receipt.get("total_amount", -1)) != listed_total:
            problems.append(
                f"receipts[{position}] lists total {listed_total} but {receipt_rel} says "
                f"total_amount {receipt.get('total_amount')}"
            )
        if int(receipt.get("action_count", -1)) != listed_actions:
            problems.append(
                f"receipts[{position}] lists {listed_actions} actions but {receipt_rel} says "
                f"action_count {receipt.get('action_count')}"
            )
        if int(receipt.get("execution_id", -1)) != execution_id:
            problems.append(
                f"receipts[{position}] is execution_id {execution_id} but {receipt_rel} says "
                f"{receipt.get('execution_id')}"
            )
        roots.append(root)
        entries.append(
            {
                "execution_id": execution_id,
                "trace_root": root,
                "total": listed_total,
                "actions": listed_actions,
                "receipt": receipt_rel,
                "dir": str(receipt_path.parent),
            }
        )

    recomputed = _merkle_root(roots) if len(roots) == len(index.get("receipts") or []) else None
    stored_root = str(index.get("root") or "")
    if recomputed is not None and stored_root != recomputed:
        problems.append(f"stored root {stored_root[:16] or '(none)'}… != root recomputed from the receipts {recomputed[:16]}…")
    total = sum(entry["total"] for entry in entries)
    if index.get("total") != total:
        problems.append(f"session total {index.get('total')!r} != sum of the receipts {total}")
    if index.get("receipt_count") != len(entries):
        problems.append(f"receipt_count {index.get('receipt_count')!r} != {len(entries)} receipts listed")
    if index.get("action_count") != sum(entry["actions"] for entry in entries):
        problems.append(
            f"action_count {index.get('action_count')!r} != sum of the receipts' action counts"
        )
    return {
        "present": True,
        "ok": not problems,
        "root": recomputed,
        "stored_root": stored_root,
        "total": total,
        "receipt_count": len(entries),
        "action_count": sum(entry["actions"] for entry in entries),
        "problems": problems,
        "entries": entries,
        "index": index,
    }


# ------------------------------------------------------------------------- replay
#
# A receipt is evidence for one execution, once. Presenting the same one twice is a replay,
# and a replayed receipt would make a single event look like two — the same failure the
# protocol guards against internally (`Receipt::is_replay`). `seen.json` is the guard's
# memory: the execution ids `zkagent_verify` has accepted, and the session roots it has
# accepted, each with the time and the artifact it came from.
#
# Scope, stated: this is a local guard. It stops an agent from re-verifying its own evidence
# and reporting a second acceptance; it cannot stop a third party who holds a copy of the
# files from verifying them elsewhere. Closing that needs the chain to track spent receipts,
# which is the protocol's job, not a JSON file's.


def _stamp(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return "an unknown time"


def _load_seen() -> dict:
    try:
        data = json.loads(_seen_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    for kind in ("executions", "sessions"):
        if not isinstance(data.get(kind), Mapping):
            data[kind] = {}
    return data


def _seen_records(kind: str) -> dict:
    section = _load_seen().get(kind)
    return dict(section) if isinstance(section, Mapping) else {}


def _record_acceptance(kind: str, key: str, record: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Add one acceptance to seen.json, atomically. Returns (recorded, error)."""
    seen = _load_seen()
    seen[kind][str(key)] = dict(record)
    path = _seen_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(seen, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        return False, f"could not write {SEEN_FILE}: {exc}"
    return True, None


def _refusal(kind: str, key: str, record: Mapping[str, Any]) -> str:
    label = "execution_id" if kind == "executions" else "session root"
    source = f" (from {record['receipt']})" if record.get("receipt") else ""
    return (
        f"{label} {key} was already accepted at {_stamp(record.get('accepted_at'))}{source}. "
        "The same evidence cannot be accepted twice: verifying it again is a replay, and a "
        "replayed receipt would make one execution look like two. Replay refused."
    )


# -------------------------------------------------------------------------- tools


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _need_binary() -> tuple[str | None, str | None, dict | None]:
    """(binary, error, install report). The report is None when no install was attempted."""
    binary, install = _resolve_binary()
    if binary:
        return binary, None, install
    detail = ""
    if install:
        if install.get("error"):
            detail = f" Auto-install failed: {install['error']}."
        elif install.get("output"):
            detail = f" Auto-install said: {install['output'][-300:]}"
        elif install.get("skipped"):
            detail = f" Auto-install skipped ({install['skipped']})."
    error = (
        "zkagent binary not found and the pinned auto-install did not produce one."
        + detail
        + " Install it yourself with `python3 scripts/install-zkagent.py` (sha256-pinned), "
        "or set ZKAGENT_BIN to a build's path."
    )
    return None, error, install


def tool_status(args: Mapping[str, Any] | None = None) -> str:
    binary, error, install = _need_binary()
    state = _load_state()
    pending = _pending(state)
    runs = state.get("runs") or []
    seen = _load_seen()
    payload: dict[str, Any] = {
        "plugin": PLUGIN_NAME,
        "binary": binary,
        "pending_actions": len(pending),
        "slots_per_receipt": MAX_ACTIONS,
        "runs": len(runs),
        "last_run": runs[-1] if runs else None,
        "settings": {
            "costs_source": "host config (plugins.entries settings)" if _host_costs() else "state.json costs, else DEFAULT_COSTS",
            "costs": _costs(),
        },
        "replay_guard": {
            "file": str(_seen_path()),
            "accepted_executions": len(seen["executions"]),
            "accepted_sessions": len(seen["sessions"]),
        },
    }
    if install:
        payload["install"] = install
    if error:
        payload["error"] = error
    elif binary:
        payload["zkagent"] = _version(binary)
    try:
        index = json.loads(_session_path().read_text(encoding="utf-8"))
        payload["session"] = {
            "file": str(_session_path()),
            "receipt_count": index.get("receipt_count"),
            "total": index.get("total"),
            "root": index.get("root"),
            "cross_checked": False,
        }
    except (OSError, ValueError):
        payload["session"] = {"file": str(_session_path()), "present": False}
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
    binary, error, install = _need_binary()
    if error:
        return _json({"ok": False, "error": error, "install": install} if install else {"ok": False, "error": error})

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
    # seen.json outlives state.json, so an id that was already accepted must never be handed to
    # different evidence: a cleared session continues above the highest accepted id, and an
    # explicitly requested id that is spent is refused rather than silently renumbered.
    accepted_ids = {int(key) for key in _seen_records("executions") if str(key).isdigit()}
    if run_id in accepted_ids:
        if "execution_id" in params:
            return _json(
                {
                    "ok": False,
                    "error": f"execution_id {run_id} was already accepted (see {SEEN_FILE}); a receipt id is "
                    f"evidence for one execution and cannot be reused. Use {max(accepted_ids) + 1} or any free id.",
                }
            )
        run_id = max(accepted_ids) + 1

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

    # The session index, rebuilt over every receipt this session has produced. A failure here
    # does not discard the run — the receipt is already verified — but it is reported, because
    # an index that silently did not update is exactly what the next verify would trip over.
    session_block: dict[str, Any] | None = None
    session_error: str | None = None
    try:
        index = _write_session(state["runs"])
        session_block = {
            "file": str(_session_path()),
            "receipt_count": index["receipt_count"],
            "total": index["total"],
            "root": index["root"],
            "merkle": index["merkle"]["node"],
        }
    except (RuntimeError, OSError, ValueError) as exc:
        session_error = str(exc)

    payload: dict[str, Any] = {
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
        "session": session_block,
    }
    if session_error:
        payload["session_error"] = session_error
    if install:
        payload["install"] = install
    return _json(payload)


def _verify_receipt(binary: str, run_dir: Path, now: Any = None) -> dict:
    """Run the binary's verify against one stored receipt. `ok` means accept, not just exit 0."""
    cmd = [
        "--json", "verify",
        "--keys", str(_keys_dir()),
        "--policy", str(run_dir / "policy.json"),
        "--receipt", str(run_dir / "receipt.json"),
        "--proof", str(run_dir / "proof.json"),
    ]
    if now is not None:
        cmd += ["--now", str(int(now))]
    code, out, err = _run(binary, cmd)
    verdict: dict[str, Any] = {"exit": code}
    try:
        verdict.update(json.loads(out.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        verdict["raw"] = (out or err).strip()[:400]
    verdict["ok"] = code == 0 and verdict.get("verdict") == "accept"
    return verdict


def tool_verify(args: Mapping[str, Any] | None = None) -> str:
    """Verify one receipt (`run_id`) or the whole session (`session: true`).

    Every path first cross-checks `session.json` against the receipts on disk; a present but
    disagreeing index is a hard failure, because accepting a receipt whose session index is
    corrupt would report a consistency the evidence does not have. Acceptance is then
    recorded in `seen.json`, and a second attempt at the same execution id (or session root)
    is refused as a replay.
    """
    params = dict(args or {})
    binary, error, install = _need_binary()
    base: dict[str, Any] = {"install": install} if install else {}
    if error:
        return _json({"ok": False, "error": error, **base})
    state = _load_state()
    runs = state.get("runs") or []
    if not runs:
        return _json({"ok": False, "error": "no attestation yet: call zkagent_attest first.", **base})

    report = _session_report()
    payload: dict[str, Any] = dict(base)
    payload["session"] = {key: value for key, value in report.items() if key not in ("entries", "index")}

    if report.get("present") and not report.get("ok"):
        return _json(
            {
                **payload,
                "ok": False,
                "verdict": "session_mismatch",
                "reason": "session_index_mismatch",
                "error": "the session index does not agree with the receipts on disk: "
                + "; ".join(report.get("problems") or ["unknown mismatch"])
                + ". Nothing is accepted until the index is rebuilt by zkagent_attest.",
            }
        )
    if params.get("session"):
        return _verify_session(binary, report, payload, params)
    return _verify_one(binary, runs, report, payload, params)


def _verify_one(binary: str, runs: Sequence[Mapping[str, Any]], report: Mapping[str, Any], payload: dict, params: Mapping[str, Any]) -> str:
    run_id = int(params.get("run_id", runs[-1]["run_id"]))
    run = next((r for r in runs if int(r["run_id"]) == run_id), None)
    if not run:
        return _json({**payload, "ok": False, "error": f"no attestation with execution_id {run_id}"})

    record = _seen_records("executions").get(str(run_id))
    if record:
        return _json(
            {
                **payload,
                "ok": False,
                "verdict": "replay",
                "reason": "already_accepted",
                "execution_id": run_id,
                "replayed": True,
                "accepted_at": record.get("accepted_at"),
                "error": _refusal("executions", str(run_id), record),
            }
        )

    run_dir = Path(str(run["dir"]))
    verdict = _verify_receipt(binary, run_dir, params.get("now"))
    payload = {**payload, **verdict, "execution_id": run_id}
    if verdict.get("verdict") != "accept":
        return _json(payload)

    # A receipt that accepts alone but disagrees with the index is not part of the attested
    # session: say so instead of returning a bare accept.
    entries = report.get("entries") or []
    if entries:
        entry = next((e for e in entries if int(e["execution_id"]) == run_id), None)
        if entry is None:
            return _json(
                {
                    **payload,
                    "ok": False,
                    "verdict": "session_mismatch",
                    "reason": "receipt_not_indexed",
                    "error": f"execution_id {run_id} verified alone but {SESSION_FILE} does not list it; "
                    "the index is stale, so this receipt is not part of the attested session.",
                }
            )
        on_disk = None
        try:
            on_disk = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8")).get("trace_root")
        except (OSError, ValueError):
            on_disk = None
        if str(on_disk) != str(entry["trace_root"]):
            return _json(
                {
                    **payload,
                    "ok": False,
                    "verdict": "session_mismatch",
                    "reason": "index_root_mismatch",
                    "error": f"{SESSION_FILE} lists trace_root {str(entry['trace_root'])[:16]}… for execution_id "
                    f"{run_id} but the receipt on disk says {str(on_disk)[:16]}…",
                }
            )

    recorded, record_error = _record_acceptance(
        "executions",
        str(run_id),
        {
            "accepted_at": int(time.time()),
            "trace_root": verdict.get("trace_root") or str(run.get("trace_root") or ""),
            "total": run.get("total"),
            "actions": run.get("actions"),
            "receipt": _relative(run_dir / "receipt.json"),
        },
    )
    payload["replay_guard"] = {"file": str(_seen_path()), "recorded": recorded}
    if record_error:
        payload["replay_guard"]["error"] = record_error
    return _json(payload)


def _verify_session(binary: str, report: Mapping[str, Any], payload: dict, params: Mapping[str, Any]) -> str:
    if not report.get("present"):
        return _json(
            {
                **payload,
                "ok": False,
                "verdict": "no_session",
                "reason": "session_index_missing",
                "error": report.get("error") or f"no {SESSION_FILE} to verify",
            }
        )
    root = str(report.get("root") or "")
    record = _seen_records("sessions").get(root)
    if record:
        return _json(
            {
                **payload,
                "ok": False,
                "verdict": "replay",
                "reason": "session_already_accepted",
                "root": root,
                "replayed": True,
                "accepted_at": record.get("accepted_at"),
                "error": _refusal("sessions", root, record),
            }
        )

    receipts: list[dict] = []
    for entry in report.get("entries") or []:
        verdict = _verify_receipt(binary, Path(str(entry["dir"])), params.get("now"))
        receipts.append(
            {
                "execution_id": entry["execution_id"],
                "verdict": verdict.get("verdict"),
                "reason": verdict.get("reason"),
                "exit": verdict.get("exit"),
            }
        )
    accepted = bool(receipts) and all(r["verdict"] == "accept" and r["exit"] == 0 for r in receipts)
    payload = {
        **payload,
        "ok": accepted,
        "verdict": "accept" if accepted else "reject",
        "root": root,
        "total": report.get("total"),
        "actions": report.get("action_count"),
        "receipts_verified": receipts,
    }
    if not accepted:
        payload["reason"] = "receipt_rejected"
        payload["error"] = (
            "every receipt in the session must accept; at least one did not, so the aggregate is not accepted."
        )
        return _json(payload)

    recorded, record_error = _record_acceptance(
        "sessions",
        root,
        {
            "accepted_at": int(time.time()),
            "executions": [r["execution_id"] for r in receipts],
            "total": report.get("total"),
            "receipt_count": report.get("receipt_count"),
            "receipt": _relative(_session_path()),
        },
    )
    payload["replay_guard"] = {"file": str(_seen_path()), "recorded": recorded}
    if record_error:
        payload["replay_guard"]["error"] = record_error
    return _json(payload)


def tool_disclose(args: Mapping[str, Any] | None = None) -> str:
    params = dict(args or {})
    binary, error, install = _need_binary()
    if error:
        return _json({"ok": False, "error": error, "install": install} if install else {"ok": False, "error": error})
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
    """Wire the tools and the ledger hook into the host, and keep the ctx for settings reads."""
    global _CTX, _HOST_COSTS
    _CTX = ctx  # see `_read_host_costs`: the only supported way to read the host's settings
    _HOST_COSTS = _UNSET  # a fresh context may mean a fresh config
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
            "Verify one receipt (default) or the whole session with `session: true`, cross-check the session "
            "index against the receipts on disk, and refuse an execution id or session root already accepted.",
            {
                "run_id": {"type": "integer", "description": "Execution id to verify; defaults to the newest run."},
                "session": {
                    "type": "boolean",
                    "description": "Verify every receipt in session.json plus the Merkle root and the session total.",
                },
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
