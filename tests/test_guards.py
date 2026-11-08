#!/usr/bin/env python3
"""Failure-path tests for the guards the smoke run does not exercise.

`tests/smoke.py` proves the happy path end to end against the real binary. These tests are
the other half: they force the failure and assert the *consequence*, which is where the
verdict/ledger bugs lived. They run without Hermes and without proving anything real —
`_run` is replaced by a stub that returns canned subprocess results, so each test decides
exactly which step fails and watches what the plugin does about it.

Run: python3 tests/test_guards.py
Every test here was written against a defect that `tests/smoke.py` reported OK over.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_plugin(home: Path):
    """A fresh module per test: the plugin caches its data dir and its cost table."""
    os.environ["HERMES_HOME"] = str(home)
    os.environ["ZKAGENT_AUTOINSTALL"] = "0"
    # Resolve to *something* executable so no test depends on an installed zkagent; every
    # subprocess call is stubbed below anyway.
    os.environ["ZKAGENT_BIN"] = sys.executable
    spec = importlib.util.spec_from_file_location(f"zkplugin_{home.name}", REPO / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub_run(module, *, verify_exit: int, verify_body: dict):
    """Policy/prove succeed, verify returns exactly what the test needs."""
    calls = []

    def fake(binary, args, timeout=180):
        calls.append(list(args))
        if "verify" in args:
            return verify_exit, json.dumps(verify_body), ""
        return 0, "", ""

    module._run = fake
    return calls


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zkguards-")
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(parents=True)
        self.m = load_plugin(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, count=3):
        for i in range(count):
            self.m._record("read_file", {"slot": i})

    def attest_accept(self):
        """An accepted attestation with no real proving: the plugin writes the artifacts."""
        self.record()
        stub_run(self.m, verify_exit=0, verify_body={"verdict": "accept", "reason": None})
        out = json.loads(self.m.tool_attest({"max_amount": 20}))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["verdict"], "accept")
        return out


class TestAttestationVerdict(GuardCase):
    def test_rejected_attestation_preserves_the_ledger(self):
        self.record()
        stub_run(self.m, verify_exit=1, verify_body={"verdict": "reject", "reason_code": "public_input_mismatch"})
        out = json.loads(self.m.tool_attest({"max_amount": 20}))
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["verdict"], "reject")
        self.assertEqual(len(self.m._read_rows()), 3, "a rejected attestation must not consume rows")
        self.assertEqual(self.m._load_state()["runs"], [], "a rejected run must not be recorded")
        self.assertFalse(self.m._session_path().exists(), "a rejected run must not be indexed")

    def test_exit_zero_beside_a_reject_is_not_a_success(self):
        self.record()
        stub_run(self.m, verify_exit=0, verify_body={"verdict": "reject", "reason": "forced"})
        out = json.loads(self.m.tool_attest({"max_amount": 20}))
        self.assertFalse(out["ok"], "an exit 0 beside a reject verdict is still a rejection")
        self.assertEqual(out["verdict"], "reject")
        self.assertEqual(len(self.m._read_rows()), 3)

    def test_unparseable_verifier_output_is_not_an_accept(self):
        self.record()
        self.m._run = lambda binary, args, timeout=180: (0, "not json at all", "")
        out = json.loads(self.m.tool_attest({"max_amount": 20}))
        self.assertFalse(out["ok"], out)
        self.assertEqual(len(self.m._read_rows()), 3)

    def test_accept_consumes_and_indexes(self):
        out = self.attest_accept()
        self.assertEqual(out["pending_after"], 0)
        self.assertEqual(len(self.m._load_state()["runs"]), 1)
        index = json.loads(self.m._session_path().read_text())
        self.assertEqual(index["receipt_count"], 1)
        self.assertEqual(index["receipts"][0]["execution_id"], out["execution_id"])

    def test_cap_refusal_keeps_the_ledger(self):
        self.record()
        out = json.loads(self.m.tool_attest({"max_amount": 0}))
        self.assertFalse(out["ok"])
        self.assertEqual(len(self.m._read_rows()), 3)
        self.assertEqual(self.m._load_state()["runs"], [])


class TestVerifyVerdict(GuardCase):
    def test_a_failed_subprocess_that_prints_accept_is_not_recorded(self):
        accepted = self.attest_accept()
        stub_run(self.m, verify_exit=1, verify_body={"verdict": "accept"})
        out = json.loads(self.m.tool_verify({}))
        self.assertFalse(out["ok"], "a nonzero verifier exit must never read as accepted")
        seen = self.m._load_seen()
        self.assertNotIn(str(accepted["execution_id"]), seen["executions"])

    def test_a_real_accept_is_recorded_once(self):
        accepted = self.attest_accept()
        stub_run(self.m, verify_exit=0, verify_body={"verdict": "accept"})
        out = json.loads(self.m.tool_verify({}))
        self.assertTrue(out["ok"], out)
        again = json.loads(self.m.tool_verify({}))
        self.assertEqual(again["verdict"], "replay")
        self.assertFalse(again["ok"])
        self.assertIn(str(accepted["execution_id"]), self.m._load_seen()["executions"])


class TestLedgerConcurrency(GuardCase):
    def test_a_row_appended_during_compaction_survives(self):
        m = self.m
        m._write_ledger([
            {"tool": "read_file", "target": 1, "amount": 1, "trace_digest": 11, "at": 1},
            {"tool": "terminal", "target": 4, "amount": 3, "trace_digest": 22, "at": 2},
        ])
        reading = threading.Event()
        attempted = threading.Event()
        appended = threading.Event()
        original_read_rows = m._read_rows

        def slow_read_rows():
            rows = original_read_rows()
            reading.set()
            # Let the hook thread reach its append while compaction is mid-flight.
            self.assertTrue(attempted.wait(timeout=10), "the hook never attempted its append")
            return rows

        def hook():
            self.assertTrue(reading.wait(timeout=10))
            attempted.set()
            m._append_row({"tool": "web_search", "target": 5, "amount": 2, "trace_digest": 33, "at": 3})
            appended.set()

        m._read_rows = slow_read_rows
        thread = threading.Thread(target=hook)
        thread.start()
        try:
            m._compact(2)
            thread.join(timeout=15)
        finally:
            m._read_rows = original_read_rows
        self.assertFalse(thread.is_alive(), "the hook deadlocked against compaction")
        self.assertTrue(appended.is_set())
        survivors = [r["trace_digest"] for r in original_read_rows()]
        self.assertIn(33, survivors, "a row appended during compaction was silently dropped")


class TestDisclosureBounds(GuardCase):
    def test_out_of_range_indices_are_refused(self):
        self.attest_accept()
        for index in (-1, 3, 99):
            out = json.loads(self.m.tool_disclose({"index": index}))
            self.assertFalse(out["ok"], f"index {index} should have been refused")


class TestMerkleKnownAnswer(GuardCase):
    def test_root_matches_an_independent_implementation(self):
        # Recomputed here from the documented construction, not by calling the plugin twice:
        # an expected value produced by the code under test agrees with its own bugs.
        def independent(roots):
            if not roots:
                return "0" * 64
            level = [hashlib.sha256(bytes.fromhex(r)).digest() for r in roots]
            while len(level) > 1:
                nxt = [hashlib.sha256(level[i] + level[i + 1]).digest() for i in range(0, len(level) - 1, 2)]
                if len(level) % 2:
                    nxt.append(level[-1])
                level = nxt
            return level[0].hex()

        roots = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(5)]
        for count in range(0, 6):
            self.assertEqual(self.m._merkle_root(roots[:count]), independent(roots[:count]))
        self.assertNotEqual(self.m._merkle_root(roots[:2]), self.m._merkle_root(roots[:2][::-1]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
