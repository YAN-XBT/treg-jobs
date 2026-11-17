"""Tool handlers. Every handler returns a JSON string, including on error."""

from __future__ import annotations

import json
import re
from typing import Any

from . import store, treg

DEFAULTS: dict[str, Any] = {
    "budget_usd": 2.0,
    "max_cost_usd": 0.5,
    "timeout_seconds": 120,
    "waterfall": "",
    "tool_map": "",
}
_CONFIG: dict[str, Any] = dict(DEFAULTS)


def configure(values: dict[str, Any] | None) -> None:
    _CONFIG.update({k: v for k, v in (values or {}).items() if v is not None})


def _budget_micro() -> int:
    try:
        return int(float(_CONFIG.get("budget_usd", 2.0)) * 1_000_000)
    except Exception:
        return 2_000_000


def _cap_micro() -> int:
    try:
        return int(float(_CONFIG.get("max_cost_usd", 0.5)) * 1_000_000)
    except Exception:
        return 500_000


def _timeout() -> int:
    try:
        return max(10, min(300, int(_CONFIG.get("timeout_seconds", 120))))
    except Exception:
        return 120


def _waterfall() -> str | None:
    val = str(_CONFIG.get("waterfall") or "").strip()
    return val or None


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, default=str)


def _err(message: str, **extra: Any) -> str:
    return json.dumps({"ok": False, "error": message, **extra}, default=str)


def _call(role: str, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
    kwargs.setdefault("timeout", _timeout())
    kwargs.setdefault("max_cost_micro", _cap_micro())
    kwargs.setdefault("budget_micro", _budget_micro())
    if _waterfall() and "waterfall" not in kwargs:
        kwargs["waterfall"] = _waterfall()
    return treg.call(role, **kwargs)


BOARD_HOSTS = (
    "linkedin.com", "greenhouse.io", "lever.co", "ashbyhq.com", "join.com",
    "workable.com", "smartrecruiters.com", "indeed.com", "glassdoor.com",
    "recruitee.com", "personio.", "bamboohr.com", "myworkdayjobs.com", "jobs.",
)


def _employer_domain(url: str) -> str:
    """The employer's own domain, or "" when the URL points at a job board."""
    host = re.sub(r"^https?://", "", (url or "").strip().lower()).split("/")[0]
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return ""
    if any(host == b.rstrip(".") or host.endswith(b) or (b.startswith("jobs.") and host.startswith("jobs.")) for b in BOARD_HOSTS):
        return ""
    return host


def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"[\s,]+", (query or "").strip().lower()) if len(t) > 1]


def _unwrap(answer: Any) -> Any:
    """treg answers come two ways: wrapped ({output: …}) for routed tools, flat for
    the data tools. Both are handled here so a handler never guesses."""
    if isinstance(answer, dict):
        out = answer.get("output")
        if isinstance(out, (dict, list)):
            return out
        return answer
    return answer


def _norm_job(j: dict[str, Any]) -> dict[str, Any]:
    """One provider-native row -> the local shape."""
    desc = str(j.get("description") or "")
    plain = re.sub(r"<[^>]+>", " ", desc)
    plain = re.sub(r"\s+", " ", plain).strip()
    return {
        "id": str(j.get("_id") or j.get("id") or j.get("platform_job_id") or ""),
        "company": str(j.get("company_name") or ""),
        "title": str(j.get("job_title") or j.get("title") or ""),
        "location": str(j.get("location_raw") or j.get("location") or ""),
        "location_type": str(j.get("location_type") or ""),
        "url": str(j.get("application_link") or j.get("url") or ""),
        "board": str(j.get("job_board") or ""),
        "posted": str(j.get("date_posted") or j.get("published") or ""),
        "description": plain,
    }


def _rows_from_answer(answer: Any) -> list[dict[str, Any]]:
    out = _unwrap(answer)
    if isinstance(out, dict):
        for key in ("jobs", "data", "results", "items"):
            if isinstance(out.get(key), list):
                return [_norm_job(j) for j in out[key] if isinstance(j, dict)]
    if isinstance(out, list):
        return [_norm_job(j) for j in out if isinstance(j, dict)]
    return []


# --------------------------------------------------------------------------- search


def jobs_search(args: dict, **kwargs: Any) -> str:
    try:
        body: dict[str, Any] = {}
        if str(args.get("query") or "").strip():
            body["title"] = str(args["query"]).strip()
        if str(args.get("company") or "").strip():
            body["name"] = str(args["company"]).strip()
        if str(args.get("domain") or "").strip():
            body["domain"] = str(args["domain"]).strip()
        if str(args.get("country") or "").strip():
            body["country"] = str(args["country"]).strip()
        if str(args.get("location") or "").strip():
            body["location"] = str(args["location"]).strip()
        if args.get("remote") is True:
            body["remote"] = True
        body["posted_within_days"] = int(args.get("days") or 30)
        body["limit"] = max(1, min(50, int(args.get("limit") or 10)))
        if not body.get("title") and not body.get("name") and not body.get("domain"):
            return _err(
                "give at least one of query, company or domain",
                hint="a bare country filter is not a search — name a title or an employer",
            )

        answer, meta = _call("search", body=body)
        rows = _rows_from_answer(answer)
        new = store.upsert_jobs(rows, source=meta.get("served_by") or "search")
        return _ok({
            "returned": len(rows),
            "new_since_last_seen": new,
            "cost": meta,
            "jobs": rows[: body["limit"]],
            "note": "pass an id to jobs_people / jobs_tracker; roles are cached locally so a repeat check only pays once",
        })
    except treg.TregError as e:
        return _err(e.message, hint=e.hint, status=e.status)
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- match


def jobs_match(args: dict, **kwargs: Any) -> str:
    try:
        profile = args.get("profile")
        if not isinstance(profile, dict) or not profile:
            profile = store.get_profile()
        if not profile:
            return _err("no profile", hint="pass profile={summary:'…', titles:[…], locations:[…]}")
        summary = str(profile.get("summary") or "").strip()
        if not summary:
            bits = [str(x) for x in (profile.get("titles") or [])] + [str(x) for x in (profile.get("skills") or [])]
            summary = ", ".join(b for b in bits if b) or "job seeker"
        store.set_profile(profile)

        lexical: dict[str, Any] = {}
        if profile.get("titles"):
            lexical["job_titles"] = [str(t) for t in profile["titles"]][:5]
        if profile.get("locations"):
            locs = [str(l) for l in profile["locations"]]
            remote = [l for l in locs if l.lower() == "remote"]
            if remote:
                lexical["location_types"] = ["Remote"]
            else:
                lexical["geo_locations"] = [{"city": l} for l in locs[:3]]
        lexical["days_ago"] = int(profile.get("days") or args.get("days") or 14)

        body: dict[str, Any] = {
            "vector": {"query": summary},
            "lexical": lexical,
            "limit": max(1, min(30, int(args.get("limit") or 8))),
        }
        if args.get("score_threshold") is not None:
            body["score_threshold"] = max(0.0, min(1.0, float(args["score_threshold"])))

        answer, meta = _call("match", body=body)
        rows = _rows_from_answer(answer)
        new = store.upsert_jobs(rows, source=meta.get("served_by") or "match")
        return _ok({
            "returned": len(rows),
            "new_since_last_seen": new,
            "cost": meta,
            "jobs": rows,
            "note": "semantic search: no similarity score is returned, so judge the rows yourself; ~15-25 s is normal",
        })
    except treg.TregError as e:
        return _err(e.message, hint=e.hint, status=e.status)
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- people


def jobs_people(args: dict, **kwargs: Any) -> str:
    try:
        target = str(args.get("company") or "").strip()
        role = str(args.get("role") or "").strip()
        limit = max(1, min(25, int(args.get("limit") or 5)))
        job = store.get_job(target)
        domain = ""
        company_name = ""
        if job:
            company_name = str(job.get("company") or "")
            domain = _employer_domain(str(job.get("url") or ""))
            role = role or str(job.get("title") or "")
        else:
            domain = _employer_domain(target) or target
            company_name = ""
        if not domain and not company_name:
            return _err("nothing to look up", hint="pass company='ramp.com' or a stored job id")

        body: dict[str, Any] = {}
        if domain:
            body["company_domain"] = domain
            if role:
                body["q"] = role
        else:
            # A posting hosted on a job board names the board in its URL, not the
            # employer — searching people at linkedin.com would be nonsense, so the
            # company name goes into the semantic query instead.
            body["q"] = " ".join(x for x in (company_name, role) if x)
        answer, meta = _call("people", body=body)
        out = _unwrap(answer) or {}
        people = out.get("people") if isinstance(out, dict) else None
        people = people or []
        slim = []
        for p in people[:limit]:
            slim.append({
                "name": p.get("name"),
                "title": p.get("title"),
                "company": p.get("company"),
                "since": p.get("title_since"),
                "location": p.get("location"),
                "profile_url": p.get("profile_url"),
                "summary": str(p.get("summary") or "")[:400],
            })
        return _ok({
            "count": len(people),
            "people": slim,
            "cost": meta,
            "note": "these are the people a provider lists at that company, not a confirmed role owner — read the titles",
            "next": "jobs_email with linkedin_url (or full_name + domain) once one person is picked",
        })
    except treg.TregError as e:
        return _err(e.message, hint=e.hint, status=e.status)
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}")


def jobs_email(args: dict, **kwargs: Any) -> str:
    try:
        body: dict[str, Any] = {}
        for key in ("domain", "full_name", "first_name", "last_name", "linkedin_url"):
            if str(args.get(key) or "").strip():
                body[key] = str(args[key]).strip()
        if not body:
            return _err("nothing to look up", hint="pass linkedin_url, or full_name with domain")
        answer, meta = _call("email", body=body)
        out = _unwrap(answer) or {}
        found = out.get("email") if isinstance(out, dict) else None
        if not found and isinstance(out, dict):
            for key in ("emails", "results", "data"):
                if out.get(key):
                    found = out[key]
                    break
        verify = (out or {}).get("verification") if isinstance(out, dict) else None
        return _ok({
            "found": bool(found),
            "result": found,
            "verification": verify,
            "cost": meta,
            "note": "a hit is the provider's best match, not a confirmation — verify before anyone sends",
            "if_empty": "do not invent an address; ask the human which person to write to instead",
        })
    except treg.TregError as e:
        return _err(e.message, hint=e.hint, status=e.status)
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- salary


def jobs_salary(args: dict, **kwargs: Any) -> str:
    try:
        title = str(args.get("title") or "").strip()
        if not title:
            return _err("title is required", hint="jobs_salary(title='backend engineer', city='Berlin', country='Germany')")
        geo: dict[str, Any] = {}
        for key in ("city", "region", "country"):
            if str(args.get(key) or "").strip():
                geo[key] = str(args[key]).strip()
        body: dict[str, Any] = {"job_titles": [title], "days_ago": max(1, min(365, int(args.get("days") or 180)))}
        if geo:
            body["geo_locations"] = [geo]
        answer, meta = _call("salary", body=body)
        out = _unwrap(answer) or {}
        return _ok({
            "headline": (out or {}).get("headline") if isinstance(out, dict) else None,
            "salary": (out or {}).get("salary") if isinstance(out, dict) else None,
            "by_level": (out or {}).get("salary_by_level") if isinstance(out, dict) else None,
            "by_location_type": (out or {}).get("salary_by_location_type") if isinstance(out, dict) else None,
            "top_companies": ((out or {}).get("top_companies") or [])[:5] if isinstance(out, dict) else None,
            "top_technologies": ((out or {}).get("top_technologies") or [])[:8] if isinstance(out, dict) else None,
            "yoe_median": (out or {}).get("yoe_median") if isinstance(out, dict) else None,
            "visa_sponsorship_rate": (out or {}).get("visa_sponsorship_rate") if isinstance(out, dict) else None,
            "cost": meta,
            "note": "quote the sample size and the share of postings that disclose pay — the median rests on those rows only",
        })
    except treg.TregError as e:
        return _err(e.message, hint=e.hint, status=e.status)
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- changes


def jobs_changes(args: dict, **kwargs: Any) -> str:
    try:
        since = str(args.get("since") or "").strip()
        limit = max(1, min(1000, int(args.get("limit") or 100)))
        if since:
            answer, meta = _call("expired", query={"since": since, "limit": limit}, method="GET")
            out = _unwrap(answer)
            rows = out if isinstance(out, list) else [out] if isinstance(out, dict) and out.get("job_title") else (out or {}).get("jobs") if isinstance(out, dict) else []
            return _ok({"since": since, "closed": len(rows or []), "rows": (rows or [])[:limit], "cost": meta})
        return _err("since is required", hint="YYYY-MM-DD inside the last 14 days, e.g. '2026-11-13'")
    except treg.TregError as e:
        return _err(e.message, hint=e.hint, status=e.status)
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- tracker


def jobs_tracker(args: dict, **kwargs: Any) -> str:
    try:
        action = str(args.get("action") or "").strip().lower()
        if action == "spend":
            if args.get("reset_spend"):
                treg.reset_spend()
            return _ok({"spent_micro_usd": treg.spent_micro(), "spent_usd": round(treg.spent_micro() / 1e6, 6),
                        "budget_usd": float(_CONFIG.get("budget_usd", 2.0)),
                        "budget_left_usd": round(treg.budget_left_micro(_budget_micro()) / 1e6, 6)})
        if action == "list":
            rows = store.tracker_rows(str(args.get("filter_status") or ""))
            return _ok({"count": len(rows), "rows": rows[:100], "db": store.db_path()})
        if action == "profile_get":
            return _ok({"profile": store.get_profile()})
        if action == "profile_set":
            profile = args.get("profile")
            if not isinstance(profile, dict) or not profile:
                return _err("profile object required")
            store.set_profile(profile)
            return _ok({"stored": True, "profile": store.get_profile()})
        if action in ("save", "status", "note"):
            job_id = str(args.get("id") or "").strip()
            if not job_id:
                return _err("id is required", hint="take an id from jobs_search")
            if not store.get_job(job_id):
                return _err(f"unknown job id '{job_id}'", hint="run jobs_search first")
            status = str(args.get("status") or "").strip().lower()
            if status and status not in store.STATUSES:
                return _err(f"unknown status '{status}'", allowed=list(store.STATUSES))
            entry = store.track(job_id, status=status, note=str(args.get("note") or ""), person=str(args.get("person") or ""))
            return _ok({"entry": entry})
        return _err("unknown action", allowed=["list", "save", "status", "note", "profile_get", "profile_set", "spend"])
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- doctor


def jobs_doctor(args: dict, **kwargs: Any) -> str:
    try:
        has_key = bool(treg.token())
        mapping = treg.tool_map_override(str(_CONFIG.get("tool_map") or "")) if hasattr(treg, "tool_map_override") else dict(treg.TOOL_MAP)
        findings = []
        if has_key:
            for role in ("search", "match", "people", "email", "salary", "expired"):
                findings.append(treg.probe(role))
        return _ok({
            "key_present": has_key,
            "key_hint": "" if has_key else "set TREG_TOKEN to the team key from https://treg.to",
            "spent_micro_usd": treg.spent_micro(),
            "budget_usd": float(_CONFIG.get("budget_usd", 2.0)),
            "budget_left_usd": round(treg.budget_left_micro(_budget_micro()) / 1e6, 6),
            "probes": findings,
            "note": "probes carry a zero cost ceiling, so they check existence without spending",
        })
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}")
