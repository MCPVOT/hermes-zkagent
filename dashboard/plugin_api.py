"""zkagent-attest — the Desktop pane's read-only backend, at ``/api/plugins/zkagent-attest/``.

Why a backend at all: the runs this pane draws live under ``plugin-data/zkagent-attest/`` on
the machine running the *gateway*, and a Desktop plugin cannot read that. ``ctx.files`` — the
SDK's one sanctioned file door — is scoped to the folder the app owns for the plugin and
refuses Hermes' own data (``plugin-data`` included) by design, so there is no bridge from a
``plugin.js`` to these artifacts. The documented path for backend files is this router: it
runs in the gateway process beside the tools that wrote them, so it reads the same
``state.json`` / ``runs/run-<id>/`` the four tools write.

Read-only, precisely:

- no route writes to the data directory, and nothing here creates it (the path is resolved
  without ``mkdir``, unlike ``plugin_storage.plugin_data_dir``);
- nothing runs ``attest`` or ``disclose`` — a disclosure artifact appears only when the agent
  asked for one with ``zkagent_disclose``;
- the one subprocess is ``zkagent verify`` (stateless: no ``--seen`` file), because the stored
  run header keeps the verdict text but not the machine-readable ``reason_code``.

Every route answers a JSON document, including for "nothing to show": the pane renders the
reason instead of a 500. Absolute paths never leave this module.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import APIRouter

PLUGIN_NAME = "zkagent-attest"
MAX_ACTIONS = 8  # zkagent-core::MAX_ACTIONS, the circuit's fixed action slots
_VERIFY_TIMEOUT = 120
_ARTIFACT_KEYS = ("total_amount", "action_count", "completed_at", "chain_id", "execution_id", "trace_root")
_POLICY_KEYS = ("max_amount", "max_per_action", "expires_at", "chain_id", "allowed_targets")

router = APIRouter()


# --------------------------------------------------------------------------- paths


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _data_dir() -> Path:
    """The plugin's own directory, resolved the way the agent half resolves it — minus the
    create: a pane that only displays must not bring a data directory into existence."""
    return _hermes_home() / "plugin-data" / PLUGIN_NAME


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _state() -> dict:
    return _read_json(_data_dir() / "state.json") or {}


def _runs(state: Mapping[str, Any]) -> list[dict]:
    runs = state.get("runs")
    return [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []


def _pending_count() -> int:
    """Rows the next attestation would consume: one line per recorded tool call in
    ``pending.jsonl`` (0.1.0 kept them inline in ``state.json``; this pane does not migrate)."""
    try:
        text = (_data_dir() / "pending.jsonl").read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _run_dir(run: Mapping[str, Any]) -> Optional[Path]:
    """The run's folder from the recorded ``dir``, but only if it really sits under this
    plugin's ``runs/`` — a doctored ``state.json`` must not turn the pane into a file reader."""
    root = (_data_dir() / "runs").resolve()
    recorded = run.get("dir")
    if isinstance(recorded, str) and recorded:
        candidate = Path(recorded)
        try:
            candidate.resolve().relative_to(root)
        except (OSError, ValueError):
            candidate = None
        if candidate is not None and candidate.is_dir():
            return candidate
    try:
        expected = root / f"run-{int(run.get('run_id'))}"
    except (TypeError, ValueError):
        return None
    return expected if expected.is_dir() else None


def _binary() -> Optional[str]:
    env = (os.environ.get("ZKAGENT_BIN") or "").strip()
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    return shutil.which("zkagent")


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _verdict(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text if text in ("accept", "reject") else None


# ------------------------------------------------------------------------ artifacts


def _disclosed_indices(run_dir: Optional[Path]) -> list[int]:
    if run_dir is None:
        return []
    found: set[int] = set()
    for path in run_dir.glob("disclosure-*.json"):
        index = _int(path.stem.rpartition("-")[2])
        if index is not None and index >= 0:
            found.add(index)
    return sorted(found)


def _run_payload(run: Mapping[str, Any], run_dir: Optional[Path]) -> dict:
    """The public statement: receipt totals first, the state header only as a fallback."""
    receipt = _read_json(run_dir / "receipt.json") if run_dir else None
    receipt = receipt or {}
    trace_root = receipt.get("trace_root") or run.get("trace_root")
    return {
        "execution_id": _int(run.get("run_id")),
        "actions": _int(receipt.get("action_count"), _int(run.get("actions"), 0)),
        "total": _int(receipt.get("total_amount"), _int(run.get("total"), 0)),
        "cap": _int(run.get("cap"), 0),
        "verdict": _verdict(run.get("verdict")),
        "reason": run.get("reason") if isinstance(run.get("reason"), str) else None,
        "at": _int(receipt.get("completed_at"), _int(run.get("at"))),
        "trace_root": trace_root if isinstance(trace_root, str) else None,
    }


def _verify(run_dir: Path, disclosure: Optional[Path] = None) -> dict:
    """``zkagent verify``, stateless. With *disclosure*, the binary also checks that the
    disclosed leaf is in the set the receipt's root commits — its result is a separate line
    on stderr, deliberately not folded into the proof verdict."""
    binary = _binary()
    if not binary:
        return {"available": False, "error": "zkagent binary not found (set ZKAGENT_BIN or put zkagent on PATH)"}
    keys = _data_dir() / "keys"
    cmd = [
        binary, "--json", "verify",
        "--keys", str(keys),
        "--policy", str(run_dir / "policy.json"),
        "--receipt", str(run_dir / "receipt.json"),
        "--proof", str(run_dir / "proof.json"),
    ]
    if disclosure is not None:
        cmd += ["--disclosure", str(disclosure)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_VERIFY_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "ok": False, "error": f"verify could not run: {type(exc).__name__}"}

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    payload: dict[str, Any] = {"available": True, "exit": proc.returncode}
    try:
        payload.update(json.loads(out.splitlines()[-1]))
    except (ValueError, IndexError):
        payload["raw"] = (out or err)[:300]
    payload["ok"] = proc.returncode == 0
    payload["disclosure"] = _disclosure_line(err)
    return payload


def _disclosure_line(stderr: str) -> Optional[dict]:
    """The binary's second verdict, read off its own line: ``disclosure ACCEPT: …`` /
    ``disclosure REJECT: <why>``. Absent when the proof rejected first (the disclosure is
    only checked under an accepted proof), which the pane must not read as "verified"."""
    for line in stderr.splitlines():
        text = line.strip()
        if text.startswith("disclosure ACCEPT"):
            return {"verdict": "accept", "detail": text}
        if text.startswith("disclosure REJECT"):
            return {"verdict": "reject", "detail": text}
    return None


# --------------------------------------------------------------------------- routes


@router.get("/latest")
def latest() -> dict:
    """The newest attestation, read-only: receipt, policy, verdict, live re-check."""
    state = _state()
    runs = _runs(state)
    payload: dict[str, Any] = {
        "ok": True,
        "attested": bool(runs),
        "pending_actions": _pending_count(),
        "slots": MAX_ACTIONS,
        "binary": _binary(),
        "run": None,
        "receipt": None,
        "policy": None,
        "disclosed": [],
        "recheck": None,
    }
    if not runs:
        return payload

    run = runs[-1]
    run_dir = _run_dir(run)
    payload["run"] = _run_payload(run, run_dir)
    if run_dir is None:
        payload["error"] = "the recorded run folder is missing from plugin-data"
        return payload

    receipt = _read_json(run_dir / "receipt.json")
    if receipt:
        payload["receipt"] = {key: receipt.get(key) for key in _ARTIFACT_KEYS}
    policy = (_read_json(run_dir / "policy.json") or {}).get("policy")
    if isinstance(policy, dict):
        payload["policy"] = {key: policy.get(key) for key in _POLICY_KEYS}
    payload["disclosed"] = _disclosed_indices(run_dir)
    payload["recheck"] = _verify(run_dir)
    return payload


@router.get("/disclosure")
def disclosure(index: int = 0) -> dict:
    """One slot's disclosure artifact plus whether its path still reaches the trace root.

    Nothing is disclosed here: an artifact exists only because ``zkagent_disclose`` ran, and
    a slot without one answers ``not-disclosed`` with the call that would create it."""
    runs = _runs(_state())
    if not runs:
        return {"ok": False, "error": "no-attestation", "message": "no attestation yet: run zkagent_attest first."}
    run = runs[-1]
    run_dir = _run_dir(run)
    if run_dir is None:
        return {"ok": False, "error": "missing-run", "message": "the recorded run folder is missing from plugin-data"}

    actions = _int(_run_payload(run, run_dir).get("actions"), 0) or 0
    if index < 0 or index >= actions:
        return {
            "ok": False,
            "error": "out-of-range",
            "message": f"slot {index} is outside this receipt's {actions} action(s)",
        }

    artifact = run_dir / f"disclosure-{index}.json"
    claim = _read_json(artifact)
    if claim is None:
        return {
            "ok": False,
            "error": "not-disclosed",
            "message": f"slot {index} has no disclosure yet — run zkagent_disclose with index {index}.",
        }

    result = _verify(run_dir, disclosure=artifact)
    checked = result.get("disclosure")
    verified: Optional[bool] = None
    detail = ""
    if checked is not None:
        verified = checked["verdict"] == "accept"
        detail = checked["detail"]
    elif result.get("ok") is False:
        detail = "the proof did not verify, so the disclosure path was not checked"
    else:
        detail = "the binary reported no disclosure line for this artifact"

    path_steps = claim.get("path")
    return {
        "ok": True,
        "index": index,
        "digest": str(claim.get("leaf_digest")),
        "root": claim.get("root"),
        "steps": len(path_steps) if isinstance(path_steps, list) else None,
        "verified": verified,
        "detail": detail,
        "checked_at": int(time.time()),
    }
