"""Deterministic offline tests for the read-only recovered Mac runtime audit."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import mac_runtime_audit as audited

NOW = 1793780000.0
STAMP = "2026-11-04T04:00:00+00:00"


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj) + "\n", encoding="utf-8")


class Fixture:
    def __init__(self, root, *, wrong_next=False, bad_model=False,
                 unsafe_permissions=False, incomplete_run=False):
        self.root = root
        self.project = root / "grok-pumpfun-cloud-recovered"
        self.db = root / "openbot.db"
        self.project.mkdir()
        write_json(self.project / "reference/MANIFEST.json", {"files": []})
        write_json(self.project / "paper_data/MANIFEST.json", {"files": []})
        self.source = {
            "captured_at_utc": STAMP,
            "total_entries": 25,
            "total_exits": 24,
        }
        snap = {
            "source": self.source,
            "review": {"verdict": "BLOCK"},
            "test_status": "PAPER_ONLY_NEEDS_INDEPENDENT_VALIDATION",
            "run_mode": "NO_SIGNING_NO_LIVE_EXECUTION",
        }
        fp = hashlib.sha256(json.dumps(snap, sort_keys=True,
                                 separators=(",", ":")).encode()).hexdigest()[:20]
        snap.update(team_fingerprint=fp, native_run_id="run-2", native_run_number=2,
                    native_status="done", native_started_at_utc=STAMP)
        write_json(self.project / "runtime/native_team_inputs/run_2.json", snap)
        self.fp = fp
        conn = sqlite3.connect(self.db)
        conn.executescript("""
        CREATE TABLE projection_agents(agent_json TEXT);
        CREATE TABLE workflows(workflow_id TEXT, title TEXT);
        CREATE TABLE workflow_nodes(workflow_id TEXT,node_id TEXT,kind TEXT,
                                    agent_id TEXT,fields_json TEXT);
        CREATE TABLE workflow_edges(workflow_id TEXT,from_node_id TEXT,
                                    to_node_id TEXT,port_type TEXT);
        CREATE TABLE workflow_runs(workflow_id TEXT,run_id TEXT,number INTEGER,
                                   status TEXT);
        CREATE TABLE workflow_steps(run_id TEXT,kind TEXT,agent_id TEXT,
                                    status TEXT,output TEXT,started_at TEXT);
        """)
        conn.execute("INSERT INTO workflows VALUES (?,?)", ("workflow-13", audited.WORKFLOW))
        model_roles = {r: "ollama/" + model
                       for model, roles in audited.MODEL_ROLES.items() for r in roles}
        self.agent_ids = {name: f"a{n}" for n, name in enumerate(set(audited.ROLES))}
        for name, agent_id in self.agent_ids.items():
            agent = {
                "id": agent_id,
                "name": name,
                "access": "network" if unsafe_permissions and name == "Boss" else "workspace",
                "computerUse": False,
                "model": model_roles[name],
            }
            conn.execute("INSERT INTO projection_agents VALUES (?)", (json.dumps(agent),))
        chain = [("node-trigger", "trigger", None)]
        for i, name in enumerate(audited.ROLES):
            chain.append((f"node-{i}", "agent", self.agent_ids[name]))
        chain.append(("node-output", "output", None))
        for node_id, kind, agent_id in chain:
            model = model_roles[audited.ROLES[int(node_id.split("-")[-1])]] if kind == "agent" else None
            if bad_model and node_id == "node-3":
                model = "ollama/qwen3:8b"
            fields = {"model": model} if model else {}
            conn.execute("INSERT INTO workflow_nodes VALUES (?,?,?,?,?)",
                         ("workflow-13", node_id, kind, agent_id, json.dumps(fields)))
        for previous, following in zip(chain, chain[1:]):
            conn.execute("INSERT INTO workflow_edges VALUES (?,?,?,?)",
                         ("workflow-13", previous[0], following[0], "text"))
        conn.execute("INSERT INTO workflow_runs VALUES (?,?,?,?)",
                     ("workflow-13", "run-2", 2, "running" if incomplete_run else "done"))
        for i, name in enumerate(audited.ROLES):
            next_role = (audited.ROLES[i + 1] if i + 1 < len(audited.ROLES)
                         else "TERMINAL_OUTPUT")
            if wrong_next and i == 1:
                next_role = "Desk"
            rows = [
                "role: " + name,
                "captured_at_utc: " + STAMP,
                "source_fingerprint: " + fp,
                "total_entries: 25",
                "total_exits: 24",
                "next_role: " + next_role,
                "verdict: BLOCK" if name != "Boss" else "verdict: BLOCKED",
            ]
            if name == "Review":
                rows.append("REVIEW_VERDICT: BLOCK")
            if name == "Boss" and i == len(audited.ROLES) - 1:
                rows.append("BOSS_DECISION: BLOCKED")
            conn.execute("INSERT INTO workflow_steps VALUES (?,?,?,?,?,?)",
                         ("run-2", "agent", self.agent_ids[name],
                          "running" if incomplete_run and i == 13 else "done",
                          "\n".join(rows), f"2026-11-04T04:00:{i:02d}+00:00"))
        conn.commit()
        conn.close()
        write_json(self.project / "runtime/live_paper/state.json", {
            "mode": "PAPER_ONLY", "execution_enabled": False,
            "status": "RUNNING", "updated_at_utc": "2026-11-04T04:12:00+00:00",
            "feed": {"pumpportal": "CONNECTED", "dexscreener": "CONNECTED"},
            "fast_monitor": {"enabled": True, "last_success_utc": "2026-11-04T04:12:00+00:00", "quotes": 100},
        })
        write_json(self.project / "runtime/paper_observation/status.json", {
            "status": "OBSERVING", "run_id": "second", "active_seconds": 35,
        })
        write_json(self.project / "runtime/paper_observation/last_censored.json", {
            "status": "CENSORED_UNOBSERVED_OR_UNSAFE", "run_id": "first",
            "active_seconds": 1781, "errors": [{"code": "MARKET_FEED_UNAVAILABLE_OR_STALE"}],
        })
        write_json(self.project / "runtime/paper_observation/independent_validation.json", {
            "verdict": "BLOCK", "passed": 8, "total": 10,
        })
        write_json(self.project / "runtime/live_readiness/latest.json", {
            "verdict": "BLOCK_REAL_MONEY", "passed": 4, "total": 14,
        })


class ReadOnlyAuditTests(unittest.TestCase):
    def fixture(self, **opts):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Fixture(Path(directory.name), **opts)

    def test_absent_machine_cannot_be_called_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            report = audited.audit(Path(folder) / "missing", Path(folder) / "missing.db", now=NOW)
        self.assertFalse(report["native"]["available"])
        self.assertFalse(report["workflow_complete"])
        self.assertFalse(report["paper_evaluation_complete"])
        self.assertFalse(report["all_engineering_verified"])
        self.assertEqual(report["trade_approval"], "BLOCK_REAL_MONEY")

    def test_frozen_complete_native_run_is_verifiable(self):
        fixture = self.fixture()
        report = audited.audit(fixture.project, fixture.db, now=1793784721.0)
        self.assertTrue(report["native"]["complete"])
        self.assertTrue(report["native"]["safe_agent_permissions"])
        self.assertTrue(report["native"]["models_aligned"])
        self.assertEqual(report["native"]["stages_completed"], 14)
        self.assertEqual(report["native"]["run_source_matches"], 14)
        self.assertEqual(report["native"]["run_handoffs_match"], 14)
        self.assertTrue(report["workflow_complete"])
        self.assertFalse(report["paper_evaluation_complete"])
        self.assertEqual(report["trade_approval"], "BLOCK_REAL_MONEY")

    def test_wrong_handoff_fails_13_agent_run(self):
        fixture = self.fixture(wrong_next=True)
        report = audited.audit(fixture.project, fixture.db, now=1793784721.0)
        self.assertEqual(report["native"]["run_source_matches"], 14)
        self.assertEqual(report["native"]["run_handoffs_match"], 13)
        self.assertFalse(report["workflow_complete"])

    def test_bad_workflow_model_is_detected(self):
        fixture = self.fixture(bad_model=True)
        report = audited.audit(fixture.project, fixture.db)
        self.assertFalse(report["native"]["models_aligned"])
        self.assertIn("native_models_aligned", report["unmet_checks"])

    def test_permission_bypass_is_detected(self):
        fixture = self.fixture(unsafe_permissions=True)
        report = audited.audit(fixture.project, fixture.db)
        self.assertFalse(report["native"]["safe_agent_permissions"])
        self.assertFalse(report["native"]["models_aligned"])

    def test_censored_run_never_counts_toward_new_observation(self):
        fixture = self.fixture()
        report = audited.audit(fixture.project, fixture.db)
        obs = report["observation"]
        self.assertEqual(obs["active_seconds"], 35)
        self.assertTrue(obs["censored_run_detected"])
        self.assertIn("MARKET_FEED_UNAVAILABLE_OR_STALE", obs["censor_reasons"])
        self.assertFalse(obs["complete_and_machine_validated"])

    def test_fresh_paper_scanner_has_verified_fast_monitor(self):
        fixture = self.fixture()
        now = audited.iso_seconds("2026-11-04T04:12:05+00:00")
        report = audited.audit(fixture.project, fixture.db, now=now)
        self.assertTrue(report["scanner"]["scanner_fresh"])
        self.assertTrue(report["scanner"]["fast_quote_fresh"])

    def test_old_terminal_run_cannot_authorize_current_observation(self):
        fixture = self.fixture()
        write_json(fixture.project / "runtime/paper_observation/last_completed.json", {
            "run_id": "first", "status": "OBSERVATION_COMPLETED_NOT_APPROVED",
            "active_seconds": 7200,
        })
        write_json(fixture.project / "runtime/paper_observation/independent_validation.json", {
            "run_id": "second", "verdict": "PASS_PAPER_ONLY", "passed": 10, "total": 10,
        })
        report = audited.audit(fixture.project, fixture.db)
        self.assertFalse(report["observation"]["complete_and_machine_validated"])

    def test_observation_7200_seconds_alone_is_not_sufficient(self):
        fixture = self.fixture()
        write_json(fixture.project / "runtime/paper_observation/last_completed.json", {
            "run_id": "second", "status": "OBSERVATION_COMPLETED_NOT_APPROVED",
            "active_seconds": 7200,
        })
        report = audited.audit(fixture.project, fixture.db)
        self.assertFalse(report["observation"]["complete_and_machine_validated"])
        self.assertEqual(report["trade_approval"], "BLOCK_REAL_MONEY")

    def test_stale_or_unsafe_paper_source_rejected(self):
        fixture = self.fixture()
        source = fixture.project / "runtime/live_paper/state.json"
        data = json.loads(source.read_text())
        data["execution_enabled"] = True
        write_json(source, data)
        report = audited.audit(fixture.project, fixture.db, now=1793784721.0)
        self.assertFalse(report["scanner"]["scanner_fresh"])
        data["execution_enabled"] = False
        data["updated_at_utc"] = "2026-11-03T00:00:00+00:00"
        write_json(source, data)
        report = audited.audit(fixture.project, fixture.db, now=1793784721.0)
        self.assertFalse(report["scanner"]["scanner_fresh"])

    def test_frozen_snapshot_tampering_detected(self):
        fixture = self.fixture()
        file = fixture.project / "runtime/native_team_inputs/run_2.json"
        data = json.loads(file.read_text())
        data["source"]["total_exits"] = 999
        write_json(file, data)
        report = audited.audit(fixture.project, fixture.db)
        self.assertFalse(report["native"]["native_loop_verified"])
        self.assertIn("NATIVE_FROZEN_SOURCE_HASH_MISMATCH", report["native"]["errors"])

    def test_invalid_clock_and_nonfinite_inputs_do_not_pass(self):
        self.assertIsNone(audited.iso_seconds("2026-11-04 13:00:00"))
        self.assertIsNone(audited.age("2027-11-04T00:00:00+00:00", NOW))
        self.assertIsNone(audited.value_number(float("nan")))
        self.assertIsNone(audited.value_number(True))
        self.assertIsNone(audited.safe_json(Path("/not/a/real/file.json")))

    def test_ambiguous_duplicate_agent_fields_fail(self):
        self.assertIsNone(audited.line_value("role: Desk\nrole: Boss", "role"))
        self.assertEqual(audited.line_value("role: Analyst\n", "role"), "Analyst")

    def test_no_sensitive_user_data_in_serialized_status(self):
        fixture = self.fixture()
        result = audited.audit(fixture.project, fixture.db)
        text = json.dumps(result)
        self.assertNotIn("AGENTS.md", text)
        self.assertNotIn("private_key", text)
        self.assertNotIn("api-key", text)


if __name__ == "__main__":
    unittest.main()
