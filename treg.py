"""treg.to HTTP client — the only way this plugin reaches the world.

Every call is one metered request to `https://treg.to/call/<id>` with
`X-Treg-Token`. Three headers matter:

  X-Treg-Token            the team key (environment, else the profile .env)
  X-Treg-Route-Max-Cost   a ceiling on THIS call, in micro-USD
  X-Treg-Route-Waterfall  "0" pays one provider only instead of every provider tried

What a call cost comes back as `x-treg-cost-micro`, and the body's `_treg` block
names the provider that actually served it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import store

BASE = "https://treg.to"
MAX_BODY = 6 * 1024 * 1024

# Verified against the live catalog (catalog_get) and with real calls on 2026-11-17.
# The marketing page lists two ids that do not exist (`people.search`,
# `people.email.find`); the routed ones below are real. Each comment records the
# request shape that actually worked.
TOOL_MAP: dict[str, str] = {
    "search": "treg.companies.jobs.search",              # POST {title,domain,name,country,location,remote,posted_within_days,limit}
    "match": "hirebase.jobs.match",                      # POST {vector:{query|job_ids}, lexical:{...}, score_threshold, page, limit}
    "people": "treg.people.search",                      # POST {q, company_domain, ...}  routed, 25 providers
    "email": "treg.people.email.find",                   # POST {domain, full_name|first+last|linkedin_url}
    "salary": "hirebase.jobs.salary.insights",            # POST {job_titles, geo_locations:[{city,region,country}], days_ago}
    "salary_report": "jobspipe.jobs.salary.benchmark",   # POST {title,country,seniority,window_days}
    "expired": "hirebase.jobs.expired",                  # GET  ?since=YYYY-MM-DD   free
    "job_get": "hirebase.jobs.get",                      # GET  ?job_id=<24-char _id>
}


class TregError(Exception):
    def __init__(self, message: str, *, status: int | None = None, hint: str = "", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.hint = hint
        self.detail = detail


def _from_env_file() -> str:
    """TREG_TOKEN from the profile's .env, if present.

    The .env fallback matters: a secret written by set_env_secret lands in the file,
    and a running gateway keeps the environment it started with.
    """
    try:
        home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
        env_file = home / ".env"
        if not env_file.exists():
            return ""
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() == "TREG_TOKEN":
                return raw.strip().strip("'").strip('"')
    except Exception:
        return ""
    return ""


def token_candidates() -> list[tuple[str, str]]:
    """Every key this install knows, newest last.

    Both sources can disagree — the environment may hold a key the gateway loaded at
    start while the file holds a rotated one — so a call tries them in preference
    order and remembers the one the API accepted.
    """
    vals: list[tuple[str, str]] = []
    file_val = _from_env_file()
    if file_val:
        vals.append((".env", file_val))
    env = os.environ.get("TREG_TOKEN", "").strip()
    if env and env != file_val:
        vals.append(("env", env))
    return vals


_PREFERRED = {"source": ""}


def token() -> str:
    cands = token_candidates()
    if not cands:
        return ""
    for source, val in cands:
        if source == _PREFERRED["source"]:
            return val
    return cands[0][1]


def tool_id(role: str) -> str:
    return TOOL_MAP.get(role, role)


def tool_map_override(raw: str) -> dict[str, str]:
    merged = dict(TOOL_MAP)
    raw = (raw or "").strip()
    if not raw:
        return merged
    try:
        patch = json.loads(raw)
    except Exception:
        return merged
    if isinstance(patch, dict):
        for key, val in patch.items():
            if isinstance(val, str) and val.strip():
                merged[str(key)] = val.strip()
    return merged


def spent_micro() -> int:
    try:
        return int(store.get_meta("spent_micro") or 0)
    except Exception:
        return 0


def reset_spend() -> None:
    store.set_meta("spent_micro", "0")


def budget_left_micro(budget_micro: int) -> int:
    return max(0, int(budget_micro) - spent_micro())


def call(
    role: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    method: str | None = None,
    timeout: int = 120,
    max_cost_micro: int = 500_000,
    budget_micro: int = 2_000_000,
    waterfall: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """One metered call, trying each known key until the API accepts one."""
    cands = token_candidates()
    if not cands:
        raise TregError(
            "TREG_TOKEN is not set",
            hint="this plugin speaks only to treg.to: put the team key in the environment (or the profile .env) and retry",
        )
    ordered = sorted(cands, key=lambda c: (c[0] != _PREFERRED["source"], c[0] != ".env"))
    last: TregError | None = None
    for source, key in ordered:
        try:
            payload, meta = _call_once(
                role, key, body=body, query=query, method=method, timeout=timeout,
                max_cost_micro=max_cost_micro, budget_micro=budget_micro, waterfall=waterfall,
            )
            _PREFERRED["source"] = source
            meta["key_source"] = source
            return payload, meta
        except TregError as e:
            if e.status != 401:
                raise
            last = e
    raise last or TregError("no usable TREG_TOKEN")


def _call_once(
    role: str,
    tok: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    method: str | None = None,
    timeout: int = 120,
    max_cost_micro: int = 500_000,
    budget_micro: int = 2_000_000,
    waterfall: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    left = budget_left_micro(budget_micro)
    if left <= 0:
        raise TregError(
            "the treg budget for this session is spent",
            hint="raise budget_usd in the plugin settings, or reset the ledger, then retry",
        )
    cap = int(min(max_cost_micro, left))

    tool = tool_id(role)
    url = f"{BASE}/call/{urllib.parse.quote(tool)}"
    if query:
        qs = urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "", [])})
        if qs:
            url = f"{url}?{qs}"
    verb = (method or ("POST" if body is not None else "GET")).upper()
    headers = {
        "X-Treg-Token": tok,
        "X-Treg-Route-Max-Cost": str(cap),
        "Accept": "application/json",
        "User-Agent": "hermes-jobs/0.2 (+https://github.com/YAN-XBT/hermes-jobs)",
    }
    if waterfall is not None:
        headers["X-Treg-Route-Waterfall"] = str(waterfall)
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()

    req = urllib.request.Request(url, data=data, headers=headers, method=verb)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BODY).decode("utf-8", "replace")
            head = {k.lower(): v for k, v in resp.headers.items()}
            payload = _decode(raw)
            cost = _int(head.get("x-treg-cost-micro"))
            if not cost and isinstance(payload, dict):
                cost = _int(((payload.get("_treg") or {}).get("charged_micro")))
            store.set_meta("spent_micro", str(spent_micro() + cost))
            treg = (payload.get("_treg") or {}) if isinstance(payload, dict) else {}
            meta = {
                "tool": tool,
                "status": resp.status,
                "cost_micro_usd": cost,
                "cost_usd": round(cost / 1_000_000, 6),
                "spent_micro_usd": spent_micro(),
                "budget_left_micro_usd": budget_left_micro(budget_micro),
                "served_by": treg.get("served_by") or head.get("x-treg-served-by") or "",
                "route_outcome": treg.get("outcome") or head.get("x-treg-route-outcome") or "",
                "providers_tried": head.get("x-treg-providers-tried") or "",
                "call_id": head.get("x-treg-call-id") or "",
                "cap_micro_usd": cap,
            }
            return payload, meta
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read(MAX_BODY).decode("utf-8", "replace")
        except Exception:
            pass
        head = {k.lower(): v for k, v in (e.headers or {}).items()}
        cost = _int(head.get("x-treg-cost-micro"))
        if cost:
            store.set_meta("spent_micro", str(spent_micro() + cost))
        msg, hint = _explain(e.code, raw, head.get("x-treg-error") or "", tool)
        raise TregError(msg, status=e.code, hint=hint, detail=raw[:1500])
    except urllib.error.URLError as e:
        raise TregError(f"network error reaching treg.to: {e.reason}",
                        hint="retry once; if it persists, a proxy or the host is blocking treg.to")
    except TregError:
        raise
    except Exception as e:  # noqa: BLE001 - the tool layer must not raise into the agent loop
        raise TregError(f"{type(e).__name__}: {e}")


def _explain(code: int, raw: str, flagged: str, tool: str) -> tuple[str, str]:
    detail = _decode(raw)
    msg = ""
    hint = ""
    if isinstance(detail, dict):
        val = detail.get("detail") or detail.get("error") or detail.get("message")
        if isinstance(val, str):
            msg = val.strip()
        elif isinstance(val, list) and val and isinstance(val[0], dict):
            first = val[0]
            msg = f"{first.get('loc')} — {first.get('msg')}"
        for key in ("hint", "advice"):
            if isinstance(detail.get(key), str):
                hint = detail[key].strip()
    if not msg:
        msg = raw.strip()[:300] or f"HTTP {code} from {tool}"
    if code == 404 and flagged:
        hint = hint or "that id is not in the catalog — run jobs_doctor to see which ids still resolve"
    elif code in (401, 403):
        hint = hint or "the team key is missing, stale, or not allowed to call this tool"
    elif code == 402 or "balance" in msg.lower():
        hint = hint or "the prepaid balance for this team is empty"
    elif code == 429:
        hint = hint or "rate limited: wait, then retry once"
    elif code >= 500:
        hint = hint or "provider-side failure: retry once, then pick another provider of the same job"
    elif 400 <= code < 500 and not hint:
        hint = hint or "the request shape was rejected — fix the arguments, do not retry unchanged"
    return (f"{tool}: {msg}", hint)


def _decode(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw[:4000]}


def _int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def probe(role: str, timeout: int = 30) -> dict[str, Any]:
    """Existence probe that cannot spend: the router may pay zero, so a paid tool
    answers with its argument error instead of running, and a missing id answers 404."""
    tool = tool_id(role)
    try:
        payload, meta = call(role, body={}, timeout=timeout, max_cost_micro=0, budget_micro=10_000_000)
        return {"role": role, "tool": tool, "verdict": "answers with no arguments",
                "cost_micro_usd": meta["cost_micro_usd"], "sample": json.dumps(payload)[:200]}
    except TregError as e:
        verdict = {
            400: "in catalog, wants arguments",
            422: "in catalog, wants arguments",
            404: "NOT in catalog",
            401: "id ok, key rejected",
            402: "in catalog, refused by the zero cost cap (expected for a probe)",
            403: "id ok, not allowed",
        }.get(e.status or 0, f"HTTP {e.status}")
        return {"role": role, "tool": tool, "verdict": verdict, "status": e.status,
                "message": e.message[:200], "hint": e.hint}
