"""hermes-jobs — the job hunt, run through treg's job API.

Eight tools over one team key: search roles across companies, semantic fit search,
the people who own a role, their work email, real salary percentiles, roles that
closed, a local pipeline tracker and a doctor. Nothing is submitted, emailed or
logged in to: every send stays the human's action.
"""

from __future__ import annotations

import logging
from typing import Any

from . import schemas, tools

logger = logging.getLogger(__name__)

PLUGIN_NAME = "hermes-jobs"
PLUGIN_ID = "hermes-jobs"
TOOLSET = "hermes_jobs"

SETTING_KEYS = ("budget_usd", "max_cost_usd", "timeout_seconds", "waterfall", "tool_map")

TOOLS = (
    ("jobs_search", schemas.JOBS_SEARCH, tools.jobs_search),
    ("jobs_match", schemas.JOBS_MATCH, tools.jobs_match),
    ("jobs_people", schemas.JOBS_PEOPLE, tools.jobs_people),
    ("jobs_email", schemas.JOBS_EMAIL, tools.jobs_email),
    ("jobs_salary", schemas.JOBS_SALARY, tools.jobs_salary),
    ("jobs_changes", schemas.JOBS_CHANGES, tools.jobs_changes),
    ("jobs_tracker", schemas.JOBS_TRACKER, tools.jobs_tracker),
    ("jobs_doctor", schemas.JOBS_DOCTOR, tools.jobs_doctor),
)


def _read_settings(ctx: Any) -> dict[str, Any]:
    # ctx.get_config is a per-key getter: a bare get_config() returns None and
    # silently yields an empty dict, so every key is read explicitly.
    values: dict[str, Any] = {}
    for key in SETTING_KEYS:
        try:
            val = ctx.get_config(key, None)
        except Exception:
            val = None
        if val is not None:
            values[key] = val
    return values


def register(ctx: Any) -> None:
    settings = _read_settings(ctx)
    tools.configure(settings)
    try:
        logger.info("%s: registering %d tools, settings=%s", PLUGIN_NAME, len(TOOLS), sorted(settings))
    except Exception:
        pass
    for name, schema, handler in TOOLS:
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler)
