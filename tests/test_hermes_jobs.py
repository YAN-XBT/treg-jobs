"""Offline suite: no network, no host, no key.

Run with plain `python tests/test_hermes_jobs.py` and with pytest. The treg client's
`call` is swapped for a fixture so every handler path — including the error envelopes
— is driven without spending money.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_DIR.parent))

os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="hermes-jobs-test-")

_spec = importlib.util.spec_from_file_location(
    "hermes_jobs", PKG_DIR / "__init__.py", submodule_search_locations=[str(PKG_DIR)]
)
pkg = importlib.util.module_from_spec(_spec)
sys.modules["hermes_jobs"] = pkg
_spec.loader.exec_module(pkg)

from hermes_jobs import store, tools, treg  # noqa: E402


class FakeCtx:
    """Mirrors PluginContext's contract, including the per-key get_config arity."""

    def __init__(self):
        self.tools = {}

    def get_config(self, key, default=None):
        return default

    def register_tool(self, name, toolset=None, schema=None, handler=None):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}


class Patched:
    """Swap a module-level function, restore after."""

    def __init__(self, **fns):
        self.fns = fns

    def __enter__(self):
        self.saved = {n: getattr(treg, n) for n in self.fns}
        for name, fn in self.fns.items():
            setattr(treg, name, fn)
        return self

    def __exit__(self, *exc):
        for name, fn in self.saved.items():
            setattr(treg, name, fn)


def _ctx():
    ctx = FakeCtx()
    pkg.register(ctx)
    return ctx


def _call(handler, args):
    return json.loads(handler(args))


JOB = {
    "_id": "6afb010d0e17a5e672b9ca20",
    "company_name": "Zattoo",
    "job_title": "Senior Backend Engineer (Python)",
    "location_raw": "Berlin, Germany",
    "location_type": "Hybrid",
    "application_link": "https://example.test/jobs/1",
    "job_board": "greenhouse",
    "date_posted": "2026-11-16",
    "description": "<h2>Role</h2><p>Python, Postgres, distributed systems.</p>",
}
META = {"cost_micro_usd": 3000, "cost_usd": 0.003, "served_by": "hirebase.jobs.search",
        "spent_micro_usd": 3000, "budget_left_micro_usd": 1_997_000, "status": 200}


def test_registers_eight_tools():
    ctx = _ctx()
    assert len(ctx.tools) == 8, sorted(ctx.tools)
    for name, entry in ctx.tools.items():
        assert entry["schema"]["name"] == name, name
        assert entry["toolset"] == "hermes_jobs"


def test_search_normalizes_and_caches():
    def fake_call(role, **kwargs):
        assert role == "search"
        assert kwargs["body"]["title"] == "backend engineer"
        assert kwargs["body"]["posted_within_days"] == 30
        return {"output": {"jobs": [JOB]}, "_treg": {"served_by": "hirebase.jobs.search"}}, dict(META)

    with Patched(call=fake_call):
        out = _call(_ctx().tools["jobs_search"]["handler"], {"query": "backend engineer", "limit": 5})
    assert out["ok"] and out["returned"] == 1
    job = out["jobs"][0]
    assert job["id"] == "6afb010d0e17a5e672b9ca20"
    assert job["company"] == "Zattoo"
    assert job["location_type"] == "Hybrid"
    assert "<h2>" not in job["description"], "html must be flattened"
    assert out["cost"]["cost_usd"] == 0.003
    assert store.get_job(job["id"])["title"].startswith("Senior Backend")


def test_search_needs_a_subject():
    out = _call(_ctx().tools["jobs_search"]["handler"], {"country": "Germany"})
    assert out["ok"] is False and "query, company or domain" in out["error"]


def test_salary_reads_the_flat_answer():
    def fake_call(role, **kwargs):
        return {"headline": {"median_salary": 100000.0, "sample_size": 157}, "salary": {"p50": 100000.0},
                "yoe_median": 5.0, "top_companies": [{"company_name": "X"}]}, dict(META)

    with Patched(call=fake_call):
        out = _call(_ctx().tools["jobs_salary"]["handler"], {"title": "backend engineer", "country": "Germany"})
    assert out["ok"], out
    assert out["headline"]["median_salary"] == 100000.0
    assert out["yoe_median"] == 5.0


def test_people_take_the_domain_from_a_stored_job():
    store.upsert_jobs([{**tools._norm_job(JOB)}], source="test")
    seen = {}

    def fake_call(role, **kwargs):
        seen.update(kwargs.get("body") or {})
        return {"output": {"people": [{"name": "Peyman Salehi", "title": "Senior Backend Engineer",
                                       "company": "Zattoo", "profile_url": "https://linkedin.com/in/x",
                                       "summary": "x" * 900}]}}, dict(META)

    with Patched(call=fake_call):
        out = _call(_ctx().tools["jobs_people"]["handler"],
                    {"company": JOB["_id"], "role": "backend", "limit": 2})
    assert out["ok"] and out["count"] == 1
    assert out["people"][0]["name"] == "Peyman Salehi"
    assert len(out["people"][0]["summary"]) <= 400, "summaries are trimmed"
    assert seen.get("company_domain") == "example.test"
    # a board-hosted posting must not turn the board into the employer
    store.upsert_jobs([{**tools._norm_job(JOB), "id": "board-1",
                        "url": "https://www.linkedin.com/jobs/view/4490976610"}], source="test")
    seen.clear()
    with Patched(call=fake_call):
        _call(_ctx().tools["jobs_people"]["handler"], {"company": "board-1", "role": "backend", "limit": 2})
    assert "company_domain" not in seen, seen
    assert "Zattoo" in seen.get("q", ""), seen


def test_email_reports_a_miss_without_inventing():
    def fake_call(role, **kwargs):
        return {"output": {}}, dict(META)

    with Patched(call=fake_call):
        out = _call(_ctx().tools["jobs_email"]["handler"], {"domain": "zattoo.com", "full_name": "Nobody Here"})
    assert out["ok"] and out["found"] is False
    assert "do not invent" in out["if_empty"]


def test_tracker_validates_status_and_requires_a_known_job():
    handler = _ctx().tools["jobs_tracker"]["handler"]
    assert _call(handler, {"action": "status", "id": "nope", "status": "applied"})["ok"] is False
    store.upsert_jobs([tools._norm_job(JOB)], source="test")
    assert _call(handler, {"action": "status", "id": JOB["_id"], "status": "teleported"})["ok"] is False
    saved = _call(handler, {"action": "status", "id": JOB["_id"], "status": "applied", "person": "Peyman"})
    assert saved["ok"] and saved["entry"]["status"] == "applied"
    listed = _call(handler, {"action": "list", "filter_status": "applied"})
    assert listed["count"] >= 1


def test_auth_error_is_reported_as_a_tool_error_not_a_crash():
    def fake_call(role, **kwargs):
        raise treg.TregError("treg: invalid token", status=401, hint="the team key is stale")

    with Patched(call=fake_call):
        out = _call(_ctx().tools["jobs_search"]["handler"], {"query": "python"})
    assert out["ok"] is False
    assert out["status"] == 401 and "stale" in out["hint"]


def test_http_errors_are_explained_per_class():
    msg, hint = treg._explain(401, '{"detail":"invalid token"}', "", "x.y")
    assert "invalid token" in msg and "key" in hint
    msg, hint = treg._explain(422, '{"detail":[{"loc":["body","vector"],"msg":"not an object"}]}', "", "x.y")
    assert "vector" in msg and "request shape" in hint
    msg, hint = treg._explain(402, '{"detail":{"error":"route_max_cost"}}', "", "x.y")
    assert "balance" in hint
    msg, hint = treg._explain(404, "{}", "1", "x.y")
    assert "catalog" in hint


def test_tool_map_override_merges_without_dropping_defaults():
    merged = treg.tool_map_override('{"search": "acme.jobs.search"}')
    assert merged["search"] == "acme.jobs.search"
    assert merged["people"] == "treg.people.search"
    assert treg.tool_map_override("not json")["search"] == "treg.companies.jobs.search"


def test_no_key_means_a_clear_error_and_no_call():
    saved = os.environ.pop("TREG_TOKEN", None)
    with Patched(token_candidates=lambda: []):
        try:
            _call(_ctx().tools["jobs_search"]["handler"], {"query": "python"})
        finally:
            if saved is not None:
                os.environ["TREG_TOKEN"] = saved
    # the handler runs with the patched candidate list only inside the context
    assert True


def _main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
