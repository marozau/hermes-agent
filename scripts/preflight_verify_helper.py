#!/usr/bin/env python3
"""preflight_verify_helper — emits Story 7.7 self-report lines for `verify`.

Reads recent preflight telemetry + cited entry_ids, then prints two lines
the `verify` skill appends to its self-report:

    preflight applied: yes|no|partial
    preflight-cited: <id1, id2, id3 | none>

Optionally records the verify-cited follow-through (Story 7.x). When verify
passes `--cited-ids "<id1,id2>"`, the helper writes a `verify_citation`
event row to today's preflight log so item-7 hit-rate can be computed by
joining preflight rows with verify_citation events on (session_id,
intent_hash).

Usage:
    python preflight_verify_helper.py <session_id>
    python preflight_verify_helper.py <session_id> --cited-ids "9267,2189"
    python preflight_verify_helper.py <session_id> --cited-ids none
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _preflight_dir() -> Path:
    import os
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "preflight"


def _today_log() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _preflight_dir() / "log" / f"{today}.jsonl"


def _last_telemetry_for(session_id: str) -> dict | None:
    log = _today_log()
    if not log.exists():
        return None
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") == "verify_citation":
            continue  # skip our own follow-through events
        if row.get("session_id") == session_id:
            rows.append(row)
    return rows[-1] if rows else None


def _citations_for(session_id: str) -> list[str]:
    p = _preflight_dir() / "last-cited.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(data.get(session_id, {}).get("entry_ids", []))


def _parse_cited_ids(arg: str | None) -> list[str] | None:
    if arg is None:
        return None
    arg = arg.strip()
    if arg.lower() in ("none", ""):
        return []
    return [s.strip() for s in arg.split(",") if s.strip()]


def _record_verify_citation(
    session_id: str, intent_hash: str, cited_ids: list[str]
) -> None:
    """Append a verify_citation event row. Schema matches
    lib.hermes_preflight.record_verify_citations so the dev-tree library
    function can be a drop-in replacement once it's importable here."""
    log = _today_log()
    log.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    row = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "verify_citation",
        "session_id": session_id,
        "intent_hash": intent_hash,
        "cited_ids": list(cited_ids),
    }, ensure_ascii=False, sort_keys=True) + "\n"
    import os
    fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, row.encode("utf-8"))
    finally:
        os.close(fd)


def emit(session_id: str, verify_cited: list[str] | None = None) -> None:
    telemetry = _last_telemetry_for(session_id)
    preflight_top = _citations_for(session_id)

    if telemetry is None:
        applied = "no"
    elif telemetry.get("skip_reason"):
        applied = "no"
    elif telemetry.get("mode") == "shadow":
        applied = "partial"  # telemetry-only, no injection
    elif preflight_top:
        applied = "yes"
    else:
        applied = "no"

    # When verify passes its own cited_ids, record the follow-through event
    # so item-7 hit-rate can be computed offline. Only records when there's
    # a preflight row to join against; otherwise the event has nothing to
    # measure.
    if verify_cited is not None and telemetry is not None:
        intent_hash = telemetry.get("intent_hash", "")
        _record_verify_citation(session_id, intent_hash, verify_cited)

    # The "preflight-cited" line is what preflight SUGGESTED (top-K). When
    # verify provides its own cited set, prefer that — it's the better
    # signal for the trajectory writer. Fall back to preflight's top-K for
    # backward compatibility.
    display_cited = verify_cited if verify_cited is not None else preflight_top
    cited_str = ", ".join(display_cited) if display_cited else "none"
    print(f"preflight applied: {applied}")
    print(f"preflight-cited: {cited_str}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    parser.add_argument(
        "--cited-ids",
        help=(
            "Comma-separated entry IDs verify actually consulted. "
            "Pass 'none' to record an explicit empty set. "
            "When omitted, the helper emits preflight's top-K as the "
            "cited line (legacy behavior) and skips the follow-through write."
        ),
    )
    args = parser.parse_args()
    emit(args.session_id, _parse_cited_ids(args.cited_ids))


if __name__ == "__main__":
    main()
