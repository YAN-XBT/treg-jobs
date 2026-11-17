"""Backend for the Jobs pane.

Mounted at /api/plugins/treg-jobs/. The gateway imports this module in the same
process as the plugin, so it reaches the SAME module objects the tools use — the
pane reads live state, not a copy of it.

A handler never raises: a failure comes back as {"ok": False, "error": "..."} so the
pane can print the reason instead of showing an opaque 500.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def _pkg() -> Any:
    """The live plugin package, resolved by path (never by name)."""
    for name, module in list(sys.modules.items()):
        if getattr(module, "__name__", "").startswith("__main__"):
            continue
        f = getattr(module, "__file__", None)
        if not f:
            continue
        try:
            if Path(f).resolve().parent != PACKAGE_DIR:
                continue
        except Exception:
            continue
        if hasattr(module, "store") and hasattr(module, "treg"):
            return module
    return None


def _salary() -> dict[str, Any] | None:
    """The last salary answer, kept by the jobs_salary tool."""
    try:
        raw = _pkg().store.get_meta("salary_snapshot")
        if not raw:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _fail(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/health")
def health() -> dict[str, Any]:
    try:
        pkg = _pkg()
        return {"ok": bool(pkg), "package": pkg.__name__ if pkg else "not loaded"}
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@router.get("/state")
def state() -> dict[str, Any]:
    """Everything the pane draws: spend, pipeline, counts, key presence."""
    try:
        pkg = _pkg()
        if pkg is None:
            return {"ok": False, "error": "plugin package not loaded in this gateway — restart the app"}
        store, treg, tools = pkg.store, pkg.treg, pkg.tools

        spent = treg.spent_micro()
        budget = float(tools._CONFIG.get("budget_usd", 2.0))  # noqa: SLF001 - same package
        rows = store.tracker_rows()
        columns: dict[str, list[dict[str, Any]]] = {s: [] for s in store.STATUSES}
        for r in rows:
            columns.setdefault(r.get("status") or "saved", []).append(r)

        jobs_total = len(store.list_jobs(limit=100000))
        return {
            "ok": True,
            "spend": {
                "spent_usd": round(spent / 1_000_000, 6),
                "budget_usd": budget,
                "left_usd": round(max(0, int(budget * 1_000_000) - spent) / 1_000_000, 6),
            },
            "key_present": bool(treg.token()),
            "pipeline": columns,
            "profile": store.get_profile(),
            "salary": _salary(),
            "tracked": len(rows),
            "jobs_cached": jobs_total,
            "db": store.db_path(),
        }
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@router.post("/move")
def move(payload: dict[str, Any]) -> dict[str, Any]:
    """Move a role between columns. Local only — no network, no cost."""
    try:
        pkg = _pkg()
        if pkg is None:
            return {"ok": False, "error": "plugin package not loaded in this gateway"}
        job_id = str(payload.get("id") or "").strip()
        status = str(payload.get("status") or "").strip().lower()
        if not job_id:
            return {"ok": False, "error": "id is required"}
        if status and status not in pkg.store.STATUSES:
            return {"ok": False, "error": f"unknown status '{status}'", "allowed": list(pkg.store.STATUSES)}
        if not pkg.store.get_job(job_id):
            return {"ok": False, "error": f"unknown job id '{job_id}'"}
        entry = pkg.store.track(job_id, status=status, note=str(payload.get("note") or ""),
                                person=str(payload.get("person") or ""))
        return {"ok": True, "entry": entry}
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@router.get("/job/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    """One posting plus the tracker row beside it."""
    try:
        pkg = _pkg()
        if pkg is None:
            return {"ok": False, "error": "plugin package not loaded in this gateway"}
        row = pkg.store.get_job(job_id)
        if not row:
            return {"ok": False, "error": f"unknown job id '{job_id}'"}
        tracked = {r["job_id"]: r for r in pkg.store.tracker_rows()}.get(job_id)
        return {"ok": True, "job": row, "tracker": tracked, "profile": pkg.store.get_profile()}
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@router.post("/profile")
def profile(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        pkg = _pkg()
        if pkg is None:
            return {"ok": False, "error": "plugin package not loaded in this gateway"}
        prof = payload.get("profile")
        if not isinstance(prof, dict) or not prof:
            return {"ok": False, "error": "profile object required"}
        pkg.store.set_profile(prof)
        return {"ok": True, "profile": pkg.store.get_profile()}
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@router.post("/reset-spend")
def reset_spend() -> dict[str, Any]:
    try:
        pkg = _pkg()
        if pkg is None:
            return {"ok": False, "error": "plugin package not loaded in this gateway"}
        pkg.treg.reset_spend()
        return {"ok": True, "spent_usd": 0.0}
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
