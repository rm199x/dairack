"""Small, bounded online calibration for coordinator model ranking.

Evidence is kept at two levels: a coarse (model, role) record that generalizes
across related work, and a finer (model, role, task kind) record that sharpens
the signal for a specific kind of task. Read-back blends both — the kind
component only contributes once it has real evidence, so sparse kinds fall back
to the coarse record instead of reacting to noise.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

from ..config import atomic_write_json

SCHEMA_VERSION = 1
MAX_ADJUSTMENT = 0.06
KIND_ADJUSTMENT = 0.05
MAX_TOTAL_ADJUSTMENT = 0.10
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


def _kind_key(model: str, role: str, kind: str) -> str:
    return f"{_record_key(model, role)}|{kind.strip().lower()}"


def _nonnegative_number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, parsed) if math.isfinite(parsed) else 0.0


def _component(record: Any, cap: float) -> tuple[float, float]:
    """One record's bounded contribution and its evidence mass."""
    if not isinstance(record, dict):
        return 0.0, 0.0
    positive = _nonnegative_number(record.get("positive"))
    negative = _nonnegative_number(record.get("negative"))
    evidence = positive + negative
    if evidence < MIN_EVIDENCE:
        return 0.0, evidence
    balance = (positive - negative) / (evidence + PRIOR_EVIDENCE)
    return max(-cap, min(cap, balance * cap)), evidence


def adjustment(state: dict[str, Any], model: str, role: str, kind: str = "") -> tuple[float, float]:
    records = state.get("records")
    if not isinstance(records, dict):
        return 0.0, 0.0
    learned, evidence = _component(records.get(_record_key(model, role)), MAX_ADJUSTMENT)
    if kind.strip():
        kind_learned, _kind_evidence = _component(records.get(_kind_key(model, role, kind)), KIND_ADJUSTMENT)
        learned = max(-MAX_TOTAL_ADJUSTMENT, min(MAX_TOTAL_ADJUSTMENT, learned + kind_learned))
    return learned, evidence


def _apply(
    records: dict[str, Any], key: str, fields: dict[str, str], reward: float, weight: float, source: str
) -> None:
    record = records.get(key)
    if not isinstance(record, dict):
        record = {**fields, "positive": 0.0, "negative": 0.0, "events": 0, "sources": {}}
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


def observe(
    path: Path,
    model: str,
    role: str,
    reward: float,
    *,
    weight: float = 1.0,
    source: str = "outcome",
    kind: str = "",
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
    kind = kind.strip()
    with _LOCK:
        state = load_state(path)
        records = state["records"]
        _apply(records, _record_key(model, role), {"model": model, "role": role}, reward, weight, source)
        if kind:
            _apply(
                records,
                _kind_key(model, role, kind),
                {"model": model, "role": role, "kind": kind},
                reward,
                weight,
                source,
            )
        atomic_write_json(path, state)
        return adjustment(state, model, role, kind)


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
    rows = [
        "COORDINATOR LEARNING",
        f"Bounded adjustment: +/-{MAX_ADJUSTMENT:.3f} per role, +/-{MAX_TOTAL_ADJUSTMENT:.3f} with task-kind evidence",
    ]
    rendered: list[tuple[float, str]] = []
    for record in records.values():
        if not isinstance(record, dict):
            continue
        model = str(record.get("model") or "unknown")
        role = str(record.get("role") or "general")
        kind = str(record.get("kind") or "")
        learned, evidence = _component(record, KIND_ADJUSTMENT if kind else MAX_ADJUSTMENT)
        sources = record.get("sources")
        source_text = (
            ", ".join(f"{name} {count}" for name, count in sorted(sources.items())) if isinstance(sources, dict) else ""
        )
        label = f"{model}  /  {role}" + (f"  /  {kind}" if kind else "")
        rendered.append(
            (
                evidence,
                f"{label}  {learned:+.3f}  /  evidence {evidence:.1f}" + (f"  /  {source_text}" if source_text else ""),
            )
        )
    rows.extend(row for _evidence, row in sorted(rendered, reverse=True))
    return "\n".join(rows)
