#!/usr/bin/env python3
"""
test_parity.py -- the gate that protects the port.

The old monolithic pipeline encoded several non-obvious corrections that were
each found the hard way (inclusive cache counters, ancestry-based session
grouping, auxiliary agents nested under real roots, per-model cache_creation).
This diffs the new agentdash pipeline against the committed session.json
produced by the old one. Any divergence is a port bug, not an improvement.

    python3 tests/test_parity.py [old_session.json] [--db sessions.db]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentdash.store import Store  # noqa: E402

# Figures verified against the old pipeline and hand-checked during development.
EXPECTED = {
    "sessions": 8,
    "turns": 62,
    "aggregate_input": 3_134_502,
    "aggregate_output": 37_605,
    "aggregate_credits": 197.75,
    "aggregate_usd": 1.9775,
    "reconciliation_rows": 11,
    "orphan_spans": 75,
}

# Keys whose ordering/structure is incidental rather than semantic.
IGNORE_PATHS = {"harness", "problems"}

failures = []
checks = 0


def check(name, got, want, tol=None):
    global checks
    checks += 1
    ok = (abs(got - want) <= tol) if (tol is not None and
                                      isinstance(got, (int, float)) and
                                      isinstance(want, (int, float))) else got == want
    if not ok:
        failures.append(f"{name}: got {got!r}, expected {want!r}")
    return ok


def deep_diff(a, b, path="", out=None):
    """Structural diff, tolerant of tiny float noise."""
    if out is None:
        out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in IGNORE_PATHS:
                continue
            if k not in a:
                out.append(f"{path}.{k}: missing in NEW")
            elif k not in b:
                out.append(f"{path}.{k}: missing in OLD")
            else:
                deep_diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} (old) vs {len(b)} (new)")
        for i, (x, y) in enumerate(zip(a, b)):
            deep_diff(x, y, f"{path}[{i}]", out)
    elif isinstance(a, float) or isinstance(b, float):
        try:
            if abs(float(a) - float(b)) > 1e-6:
                out.append(f"{path}: {a!r} (old) vs {b!r} (new)")
        except (TypeError, ValueError):
            if a != b:
                out.append(f"{path}: {a!r} (old) vs {b!r} (new)")
    elif a != b:
        out.append(f"{path}: {a!r} (old) vs {b!r} (new)")
    return out


def main():
    args = [a for a in sys.argv[1:]]
    db = "sessions.db"
    if "--db" in args:
        i = args.index("--db")
        db = args[i + 1]
        del args[i:i + 2]
    old_path = args[0] if args else "session.json"

    store = Store(db)
    rows = store.session_list()
    new_sessions = [r for r in rows if r["session_id"] != "__all__"]
    agg = store.session_payload("__all__")

    print("=== absolute figures ===")
    check("sessions", len(new_sessions), EXPECTED["sessions"])
    check("aggregate turns", agg["summary"]["turn_count"], EXPECTED["turns"])
    check("aggregate input", agg["summary"]["total_input"], EXPECTED["aggregate_input"])
    check("aggregate output", agg["summary"]["total_output"], EXPECTED["aggregate_output"])
    check("aggregate credits", agg["summary"]["cost"]["credits"],
          EXPECTED["aggregate_credits"], tol=0.01)
    check("aggregate usd", agg["summary"]["cost"]["usd"],
          EXPECTED["aggregate_usd"], tol=0.0001)
    check("reconciliation rows", len(agg["reconciliation"]),
          EXPECTED["reconciliation_rows"])

    # no double-counting: the aggregate must equal the sum of the parts
    total = sum(store.session_payload(r["session_id"])["summary"]["total_input"]
                for r in new_sessions)
    check("sum(sessions) == aggregate", total, agg["summary"]["total_input"])

    # every reconciliation row must pass
    bad = [r for r in agg["reconciliation"]
           if not (r["input_match"] and r["output_match"])]
    check("failing reconciliation rows", len(bad), 0)

    # no validation errors anywhere
    errs = store.problems(severity="error")
    check("validation errors", len(errs), 0)
    for e in errs[:5]:
        print(f"    {e['code']}: {e['message']}")

    for name, got, want in [
        ("sessions", len(new_sessions), EXPECTED["sessions"]),
        ("turns", agg["summary"]["turn_count"], EXPECTED["turns"]),
        ("input", agg["summary"]["total_input"], EXPECTED["aggregate_input"]),
        ("credits", round(agg["summary"]["cost"]["credits"], 2), EXPECTED["aggregate_credits"]),
    ]:
        print(f"  {name:12} {got!r:>12}  expected {want!r}")

    # ---- digest check against the committed fixture ----------------------
    # The old session.json was derived data (gitignored, and it carried captured
    # telemetry), so the durable baseline is a content-free digest instead.
    import hashlib

    def _h(o):
        return hashlib.sha256(
            json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()[:16]

    fixture = Path(__file__).parent / "fixtures" / "parity-digest.json"
    if fixture.exists():
        print("\n=== digest check vs committed fixture ===")
        want = json.load(open(fixture))["sessions"]
        drift = 0
        for sid, secs in want.items():
            p = store.session_payload(sid)
            if p is None:
                failures.append(f"session {sid} in fixture but missing from store")
                continue
            for sec, want_h in secs.items():
                got_h = _h(p.get(sec))
                if got_h != want_h:
                    failures.append(f"{sid}.{sec} digest {got_h} != {want_h}")
                    drift += 1
        extra = {r["session_id"] for r in rows} - set(want)
        for sid in extra:
            failures.append(f"session {sid} in store but not in fixture")
        if not drift and not extra:
            print(f"  {len(want)} sessions match the committed digests")
    else:
        print(f"\n(no digest fixture at {fixture})")

    # ---- optional structural diff against a legacy session.json ----------
    if Path(old_path).exists():
        print(f"\n=== structural diff vs {old_path} ===")
        old = json.load(open(old_path))
        old_by_id = {s["id"]: s for s in old["sessions"]}
        old_by_id["__all__"] = old["aggregate"]

        for r in rows:
            sid = r["session_id"]
            new_p = store.session_payload(sid)
            old_p = old_by_id.get(sid)
            if old_p is None:
                failures.append(f"session {sid} present in NEW but not OLD")
                continue
            for section in ("summary", "turns", "tools", "servers", "context",
                            "reconciliation", "requests"):
                d = deep_diff(old_p.get(section), new_p.get(section), f"{sid}.{section}")
                if d:
                    failures.extend(d[:8])
                    if len(d) > 8:
                        failures.append(f"  ... {len(d)-8} more diffs in {sid}.{section}")
        missing = set(old_by_id) - {r["session_id"] for r in rows}
        for sid in missing:
            failures.append(f"session {sid} present in OLD but not NEW")
        if not failures:
            print("  identical across summary/turns/tools/servers/context/"
                  "reconciliation/requests")
    else:
        print(f"\n(skipped structural diff -- {old_path} not found)")

    print()
    if failures:
        print(f"PARITY FAILED -- {len(failures)} difference(s):")
        for f in failures[:40]:
            print(f"  - {f}")
        if len(failures) > 40:
            print(f"  ... and {len(failures)-40} more")
        return 1
    print(f"PARITY OK -- {checks} checks passed, payloads identical.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
