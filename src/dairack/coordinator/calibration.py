"""Small, bounded online calibration for coordinator model ranking."""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

from ..config import atomic_write_json

SCHEMA_VERSION = 1
MAX_ADJUSTMENT = 0.06
MIN_EVIDENCE = 3.0
PRIOR_EVIDENCE = 6.0
MAX_EVIDENCE = 96.0
MAX_RECORDS = 512
_LOCK = threading.RLock()


def _empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "records": {}}


def load_state(path: Path) -> dict[str, Any]:
    with _LOCK:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _empty_state()
    if not isinstance(raw, dict) or not isinstance(raw.get("records"), dict):
        return _empty_state()
    records = [
        (str(key), value) for key, value in raw["records"].items() if isinstance(key, str) and isinstance(value, dict)
    ][-MAX_RECORDS:]
    return {"schema_version": SCHEMA_VERSION, "records": dict(records)}


def _record_key(model: str, role: str) -> str:
    return f"{model.strip().lower()}|{role.strip().lower()}"


def _nonnegative_number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, parsed) if math.isfinite(parsed) else 0.0


def adjustment(state: dict[str, Any], model: str, role: str) -> tuple[float, float]:
    records = state.get("records")
    record = records.get(_record_key(model, role), {}) if isinstance(records, dict) else {}
    if not isinstance(record, dict):
        return 0.0, 0.0
    positive = _nonnegative_number(record.get("positive"))
    negative = _nonnegative_number(record.get("negative"))
    evidence = positive + negative
    if evidence < MIN_EVIDENCE:
        return 0.0, evidence
    balance = (positive - negative) / (evidence + PRIOR_EVIDENCE)
    learned = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, balance * MAX_ADJUSTMENT))
    return learned, evidence


def observe(
    path: Path,
    model: str,
    role: str,
    reward: float,
    *,
    weight: float = 1.0,
    source: str = "outcome",
) -> tuple[float, float]:
    try:
        reward = float(reward)
        weight = float(weight)
    except (TypeError, ValueError):
        return 0.0, 0.0
    if not math.isfinite(reward) or not math.isfinite(weight):
        return 0.0, 0.0
    reward = max(-1.0, min(1.0, reward))
    weight = max(0.0, min(4.0, weight))
    if not model.strip() or not role.strip() or not weight or not reward:
        return 0.0, 0.0
    with _LOCK:
        state = load_state(path)
        records = state["records"]
        key = _record_key(model, role)
        record = records.get(key)
        if not isinstance(record, dict):
            record = {
                "model": model,
                "role": role,
                "positive": 0.0,
                "negative": 0.0,
                "events": 0,
                "sources": {},
            }
        magnitude = abs(reward) * weight
        field = "positive" if reward > 0 else "negative"
        record[field] = round(_nonnegative_number(record.get(field)) + magnitude, 3)
        evidence = _nonnegative_number(record.get("positive")) + _nonnegative_number(record.get("negative"))
        if evidence > MAX_EVIDENCE:
            scale = MAX_EVIDENCE / evidence
            record["positive"] = round(_nonnegative_number(record.get("positive")) * scale, 3)
            record["negative"] = round(_nonnegative_number(record.get("negative")) * scale, 3)
        try:
            events = max(0, int(record.get("events") or 0))
        except (TypeError, ValueError):
            events = 0
        record["events"] = events + 1
        sources = record.get("sources")
        sources = dict(sources) if isinstance(sources, dict) else {}
        sources[source] = int(sources.get(source) or 0) + 1
        record["sources"] = sources
        records[key] = record
        atomic_write_json(path, state)
        return adjustment(state, model, role)


def reset(path: Path) -> None:
    with _LOCK:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def report(path: Path) -> str:
    state = load_state(path)
    records = state.get("records")
    if not isinstance(records, dict) or not records:
        return "Coordinator learning has no evidence yet."
    rows = ["COORDINATOR LEARNING", f"Bounded adjustment: +/-{MAX_ADJUSTMENT:.3f}"]
    rendered: list[tuple[float, str]] = []
    for record in records.values():
        if not isinstance(record, dict):
            continue
        model = str(record.get("model") or "unknown")
        role = str(record.get("role") or "general")
        learned, evidence = adjustment(state, model, role)
        sources = record.get("sources")
        source_text = (
            ", ".join(f"{name} {count}" for name, count in sorted(sources.items())) if isinstance(sources, dict) else ""
        )
        rendered.append(
            (
                evidence,
                f"{model}  /  {role}  {learned:+.3f}  /  evidence {evidence:.1f}"
                + (f"  /  {source_text}" if source_text else ""),
            )
        )
    rows.extend(row for _evidence, row in sorted(rendered, reverse=True))
    return "\n".join(rows)
