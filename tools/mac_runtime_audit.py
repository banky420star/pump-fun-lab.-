#!/usr/bin/env python3
"""Fail-closed, read-only integrity audit of a recovered Grok Pump.fun Mac desk.

No external dependencies, model calls, credentials, RPC, order endpoints, or
mutable OpenBot/database operations. This is an *evidence inspector*, not a
trade executor or a promise of profitability.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from urllib.request import urlopen

ROLES = (
    "Boss", "Desk", "Analyst", "Strategy", "Fill", "Opener",
    "Opener Fast Trading", "Opener Normal Coins", "Opener Meyham",
    "Feedback", "The Fixer", "Review", "Marketing", "Boss",
)
WORKFLOW = "Pumpfun Paper \u00b7 13-Agent Research & Learning"
MODEL_ROLES = {
    "qwen3.5:4b": (
        "Desk", "Fill", "Opener", "Opener Fast Trading",
        "Opener Normal Coins", "Opener Meyham", "Marketing",
    ),
    "qwen3.5:9b": ("Boss", "Analyst", "Strategy", "Feedback", "Review"),
    "gemma4:12b-mlx": ("The Fixer",),
}
FINGERPRINT_EXCLUDED = frozenset((
    "native_run_id", "native_run_number", "native_status",
    "native_started_at_utc", "team_fingerprint",
))


def iso_seconds(value):
    """Only accept offset-aware ISO-8601 instants; never trust naive local times."""
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return date.timestamp() if date.tzinfo is not None else None


def age(value, now):
    t = iso_seconds(value)
    if t is None or not math.isfinite(now - t) or now - t < -3:
        return None
    return max(0., now - t)


def safe_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def value_number(x):
    """Reject bool, NaN, infinity and negative where count is expected."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def line_value(output, field):
    if not isinstance(output, str):
        return None
    m = re.findall(r"(?im)^\s*(?:[-*]\s*)?" + re.escape(field) +
                   r"\s*:\s*([^\r\n]+)", output)
    return m[0].strip().strip("`* ") if len(m) == 1 else None


def _inspect_graph(db_path):
    """Return a read-only snapshot. Never reflect chats or private DB fields."""
    result = {"available": False, "complete": False, "safe_agent_permissions": False,
              "models_aligned": False, "native_run": None, "stages_completed": 0,
              "native_loop_verified": False, "run_status": "NOT_FOUND",
              "run_source_matches": 0, "run_handoffs_match": 0,
              "review_veto_verified": False, "errors": []}
    if not Path(db_path).is_file():
        result["errors"].append("OPENBOT_DB_OFFLINE_OR_ABSENT")
        return result
    try:
        conn = sqlite3.connect("file:" + Path(db_path).resolve().as_posix() + "?mode=ro",
                               uri=True, timeout=2)
        conn.execute("PRAGMA query_only=ON")
        profiles = {}
        for (raw,) in conn.execute("SELECT agent_json FROM projection_agents"):
            agent = json.loads(raw)
            profiles[agent["id"]] = agent
        selected = conn.execute("SELECT workflow_id FROM workflows WHERE title=?",
                                (WORKFLOW,)).fetchone()
        if not selected:
            raise ValueError("WORKFLOW_NOT_FOUND")
        wid = selected[0]
        nodes = conn.execute("SELECT node_id,kind,agent_id,fields_json "
                             "FROM workflow_nodes WHERE workflow_id=?", (wid,)).fetchall()
        edges = conn.execute("SELECT from_node_id,to_node_id,port_type "
                             "FROM workflow_edges WHERE workflow_id=?", (wid,)).fetchall()
        names = {nid: profiles.get(aid, {}).get("name") if kind == "agent" else kind
                 for nid, kind, aid, _ in nodes}
        forward = {}
        for a, b, port in edges:
            if a in forward or port != "text":
                raise ValueError("FAN_OUT_OR_NON_TEXT_HANDOFF")
            forward[a] = b
        start = [nid for nid, name in names.items() if name == "trigger"]
        if len(start) != 1 or len(nodes) != 16 or len(edges) != 15:
            raise ValueError("INCOMPLETE_NATIVE_GRAPH")
        sequence = []
        visited = set()
        next_id = start[0]
        while next_id is not None:
            if next_id in visited or next_id not in names:
                raise ValueError("GRAPH_CYCLE_OR_BAD_EDGE")
            visited.add(next_id)
            sequence.append(names[next_id])
            next_id = forward.get(next_id)
        result["complete"] = sequence == ["trigger", *ROLES, "output"]
        if not result["complete"]:
            raise ValueError("ROLE_ORDER_NOT_VERIFIED")
        unique_names = set(ROLES)
        agents = [v for v in profiles.values() if v.get("name") in unique_names]
        result["safe_agent_permissions"] = (
            len(agents) == 13 and
            all(a.get("access") == "workspace" and a.get("computerUse") is False
                for a in agents)
        )
        expected_models = {r: "ollama/" + m
                           for m, roles in MODEL_ROLES.items() for r in roles}
        result["models_aligned"] = (
            result["safe_agent_permissions"] and
            all(a.get("model") == expected_models.get(a.get("name"))
                for a in agents) and
            all(json.loads(fields or "{}").get("model") ==
                expected_models.get(profiles.get(aid, {}).get("name"))
                for _, kind, aid, fields in nodes if kind == "agent")
        )
        run = conn.execute("SELECT run_id,number,status FROM workflow_runs "
                           "WHERE workflow_id=? ORDER BY number DESC LIMIT 1", (wid,)).fetchone()
        result["available"] = True
        if not run:
            result["run_status"] = "NOT_STARTED"
            return result
        rid, number, state = run
        result["native_run"] = number
        result["run_status"] = str(state)
        steps = conn.execute("SELECT kind,agent_id,status,output FROM workflow_steps "
                             "WHERE run_id=? ORDER BY started_at", (rid,)).fetchall()
        agent_steps = [(profiles.get(aid, {}).get("name"), status, output)
                       for kind, aid, status, output in steps if kind == "agent"]
        result["stages_completed"] = sum(state == "done" for _, state, _ in agent_steps)
        result["_private_step_data"] = (rid, number, agent_steps)
        return result
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # Do not return SQLite errors or paths that might expose user data.
        result["errors"].append("WORKFLOW_PROJECTION_INVALID_" + type(exc).__name__.upper())
        return result
    finally:
        if 'conn' in locals():
            conn.close()


def _verify_native_run(result, project):
    private = result.pop("_private_step_data", None)
    if not private:
        return
    run_id, number, steps = private
    snapshot = safe_json(project / "runtime/native_team_inputs" / f"run_{number}.json")
    if not snapshot:
        result["errors"].append("NATIVE_FROZEN_SOURCE_MISSING")
        return
    body = {k: v for k, v in snapshot.items() if k not in FINGERPRINT_EXCLUDED}
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()[:20]
    if (fingerprint != snapshot.get("team_fingerprint") or
            snapshot.get("native_run_id") != run_id or
            snapshot.get("native_run_number") != number):
        result["errors"].append("NATIVE_FROZEN_SOURCE_HASH_MISMATCH")
        return
    source = snapshot.get("source") or {}
    source_hash = snapshot["team_fingerprint"]
    t = source.get("captured_at_utc")
    entries = source.get("total_entries")
    exits = source.get("total_exits")
    if (iso_seconds(t) is None or type(entries) is not int or type(exits) is not int
            or not 0 <= exits <= entries):
        result["errors"].append("NATIVE_SNAPSHOT_COUNTERS_OR_TIME_INVALID")
        return
    source_hits, route_hits = 0, 0
    review_ok, final_boss_ok = False, False
    for i, (role, state, output) in enumerate(steps):
        if i >= len(ROLES) or role != ROLES[i] or state != "done":
            continue
        same = (line_value(output, "source_fingerprint") == source_hash and
                line_value(output, "captured_at_utc") == t and
                line_value(output, "total_entries") == str(entries) and
                line_value(output, "total_exits") == str(exits))
        destination = ROLES[i + 1] if i + 1 < len(ROLES) else "TERMINAL_OUTPUT"
        handoff = line_value(output, "next_role") == destination
        source_hits += bool(same)
        route_hits += bool(handoff)
        if role == "Review":
            review_ok = (same and handoff and
                         line_value(output, "REVIEW_VERDICT") == "BLOCK")
        if i == len(ROLES) - 1:
            final_boss_ok = (same and handoff and
                             line_value(output, "BOSS_DECISION") == "BLOCKED")
    result["run_source_matches"] = source_hits
    result["run_handoffs_match"] = route_hits
    result["review_veto_verified"] = bool(review_ok)
    result["native_loop_verified"] = bool(
        len(steps) == 14 and result["stages_completed"] == 14 and
        source_hits == 14 and route_hits == 14 and review_ok and
        final_boss_ok and result["run_status"] == "done")
    if not result["native_loop_verified"]:
        result["errors"].append("NATIVE_RUN_INCOMPLETE_OR_HANDOFF_REJECTED")


def _status(project, now):
    state = safe_json(project / "runtime/live_paper/state.json")
    result = {"available": state is not None, "scanner_fresh": False,
              "fast_quote_fresh": False, "mode": "UNVERIFIED",
              "source_age_seconds": None}
    if not state:
        return result
    result["mode"] = state.get("mode", "UNVERIFIED")
    ts_age = age(state.get("updated_at_utc"), now)
    result["source_age_seconds"] = round(ts_age, 1) if ts_age is not None else None
    safe = state.get("mode") == "PAPER_ONLY" and state.get("execution_enabled") is False
    feeds = state.get("feed") or {}
    result["scanner_fresh"] = bool(safe and state.get("status") == "RUNNING" and
        ts_age is not None and ts_age <= 85 and
        feeds.get("pumpportal") == "CONNECTED" and
        feeds.get("dexscreener") == "CONNECTED")
    fast = state.get("fast_monitor") or {}
    recent = age(fast.get("last_success_utc"), now)
    result["fast_quote_fresh"] = bool(result["scanner_fresh"] and
        fast.get("enabled") is True and recent is not None and recent <= 20 and
        (value_number(fast.get("quotes")) or 0) > 0)
    return result


def _observation(project):
    current = safe_json(project / "runtime/paper_observation/status.json") or {}
    last = safe_json(project / "runtime/paper_observation/last_completed.json") or {}
    censored = safe_json(project / "runtime/paper_observation/last_censored.json") or {}
    independent = safe_json(project / "runtime/paper_observation/independent_validation.json") or {}
    readiness = safe_json(project / "runtime/live_readiness/latest.json") or {}
    seconds = value_number(current.get("active_seconds"))
    last_seconds = value_number(last.get("active_seconds"))
    # A terminal run is not independently validated unless the checker binds
    # its decision to the SAME terminal run identifier. Historic successes
    # must never grandfather a new censored or unobserved period.
    verdict_run = independent.get("run_id") or (independent.get("metrics") or {}).get("observation_id")
    complete = bool(last.get("status") == "OBSERVATION_COMPLETED_NOT_APPROVED" and
        isinstance(last.get("run_id"), str) and last.get("run_id") and
        verdict_run == last.get("run_id") and
        last_seconds is not None and last_seconds >= 7200 and
        independent.get("verdict") in ("PASS_PAPER_ONLY", "PASS") and
        independent.get("passed") == 10 and independent.get("total") == 10)
    return {
        "observation_status": current.get("status", "NOT_STARTED"),
        "active_seconds": round(max(0., seconds or 0.), 2),
        "complete_and_machine_validated": complete,
        "censored_run_detected": censored.get("status") == "CENSORED_UNOBSERVED_OR_UNSAFE",
        "censor_reasons": [item.get("code", "UNKNOWN") for item in
            (censored.get("errors") or []) if isinstance(item, dict)][:5],
        "independent_verdict": independent.get("verdict", "NO_EVIDENCE"),
        "independent_passed": independent.get("passed"),
        "independent_total": independent.get("total"),
        "real_money_verdict": readiness.get("verdict", "BLOCK_REAL_MONEY"),
        "readiness_passed": readiness.get("passed"),
        "readiness_total": readiness.get("total"),
    }


def _dashboard(url):
    # Only the fixed loopback URL is queried; no remote URL parameter exists.
    if url is None:
        return None
    try:
        with urlopen("http://127.0.0.1:8772/api/operations", timeout=2) as res:
            if res.status != 200:
                return False
            data = json.loads(res.read(1024 * 1024))
            return data.get("execution_enabled") is False and data.get("native_team") is not None
    except (OSError, ValueError, UnicodeError):
        return False


def audit(project, db_path, *, now=None, local_dashboard=False):
    """Machine-readable recovery facts. Never marks trading readiness approved."""
    now = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    project = Path(project)
    native = _inspect_graph(db_path)
    _verify_native_run(native, project)
    scanner = _status(project, now)
    observation = _observation(project)
    dashboard = _dashboard(True) if local_dashboard else None
    engineering = {
        "source_snapshot_present": (project / "reference/MANIFEST.json").is_file(),
        "paper_data_manifest_present": (project / "paper_data/MANIFEST.json").is_file(),
        "native_graph_complete": native["complete"],
        "agent_permissions_restricted": native["safe_agent_permissions"],
        "native_models_aligned": native["models_aligned"],
        "paper_scanner_fresh": scanner["scanner_fresh"],
        "fast_quote_monitor_fresh": scanner["fast_quote_fresh"],
        "dashboard_safe_and_available": dashboard,
        "native_loop_verified": native["native_loop_verified"],
        "two_hour_observation_validated": observation["complete_and_machine_validated"],
    }
    blockers = [k for k, v in engineering.items() if v is False]
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "mode": "PAPER_ONLY_RESEARCH",
        "execution_enabled": False,
        "source_provenance_complete": engineering["source_snapshot_present"] and
            engineering["paper_data_manifest_present"],
        "engineering_checks": engineering,
        "native": native,
        "scanner": scanner,
        "observation": observation,
        "unmet_checks": blockers,
        "workflow_complete": bool(native["native_loop_verified"]),
        "paper_evaluation_complete": bool(observation["complete_and_machine_validated"]),
        "trade_approval": "BLOCK_REAL_MONEY",
        "all_engineering_verified": not blockers and dashboard is not None,
        "notes": [
            "The source and paper-data manifests are presence checks, not full hash verification.",
            "A completed native agent run is not proof of a trading edge.",
            "No trading wallet, private key, or execution endpoint was inspected.",
            "Missing/stale observations and unverified fills cannot be counted as proof.",
            "Real-money trading cannot be authorized by this audit.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path,
                        default=Path.home() / "Projects/grok-pumpfun-cloud-recovered")
    parser.add_argument("--openbot-db", type=Path,
                        default=Path.home() / "Library/Application Support/OpenBot/openbot.db")
    parser.add_argument("--check-dashboard", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional safe JSON status export")
    args = parser.parse_args(argv)
    result = audit(args.project, args.openbot_db, local_dashboard=args.check_dashboard)
    output = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if result["all_engineering_verified"] else 2


if __name__ == "__main__":
    sys.exit(main())
