"""Tool schemas — the contract the model reads."""

from __future__ import annotations

PROFILE_DOC = (
    "Profile object: {summary: '6 years backend, Go+Postgres, fintech, remote EU', "
    "titles: ['backend engineer','platform engineer'], locations: ['Remote','Berlin'], "
    "days: 14, min_salary: 80000, exclude_companies: [], exclude_keywords: []}"
)

JOBS_SEARCH = {
    "name": "jobs_search",
    "description": (
        "Search open roles across companies through treg (routed over 7 job providers, 0% markup, "
        "billed per hit). Returns provider-native rows: id, company, title, location, remote/on-site, "
        "date posted, job board, application link and the full description. Requires the user's treg "
        "team key; every result states what the call cost. Use jobs_changes afterwards to see only "
        "roles added since the last check."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Job-title words, e.g. 'backend engineer'. Omit to search by company only."},
            "company": {"type": "string", "description": "Employer name ('Ramp') — narrows to that company."},
            "domain": {"type": "string", "description": "Employer website domain ('ramp.com') — narrows to that company."},
            "country": {"type": "string", "description": "Country filter, e.g. 'Germany'."},
            "location": {"type": "string", "description": "City or region filter."},
            "remote": {"type": "boolean", "description": "true = remote only."},
            "days": {"type": "integer", "description": "Only roles posted within this many days (default 30)."},
            "limit": {"type": "integer", "description": "Rows to return (default 10, max 50). Each returned job is charged."},
        },
        "required": [],
    },
}

JOBS_MATCH = {
    "name": "jobs_match",
    "description": (
        "Semantic fit search: hand it a profile and it returns roles ranked by similarity to that text, "
        "filtered lexically (titles, locations, recency). Takes 15–25 s and returns no similarity score, "
        "so read the rows. Stored locally like jobs_search rows. Hidden cost: each returned job is charged."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile": {"type": "object", "description": PROFILE_DOC + " The summary is the semantic query; titles/locations add lexical filters."},
            "limit": {"type": "integer", "description": "How many roles (default 8, max 30)."},
            "score_threshold": {"type": "number", "description": "0–1 similarity floor, if you want a stricter cut."},
        },
        "required": ["profile"],
    },
}

JOBS_PEOPLE = {
    "name": "jobs_people",
    "description": (
        "Find who works at a company — the team that would own a role, not the recruiter. Routed over 25 "
        "providers through treg and billed per call (~$0.007 observed). Returns name, title, tenure, "
        "location, LinkedIn URL and a profile summary per person. Use it after a role is found and before "
        "writing to anyone. Any address still needs verification before a send."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "company": {"type": "string", "description": "Employer domain ('ramp.com') or the stored job id to take it from."},
            "role": {"type": "string", "description": "Who you are looking for, in words: 'engineering manager, payments'."},
            "limit": {"type": "integer", "description": "Max people returned (default 5)."},
        },
        "required": ["company"],
    },
}

JOBS_EMAIL = {
    "name": "jobs_email",
    "description": (
        "Find one person's work email through treg (routed over 32 providers, ~$0.001). Send what you know: "
        "a LinkedIn URL, or a full name plus the company domain. A hit is the provider's best match, not a "
        "confirmation — verify before sending, and never invent an address when this returns nothing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "description": "Company domain ('ramp.com')."},
            "full_name": {"type": "string", "description": "Person's full name."},
            "first_name": {"type": "string", "description": "First name, with last_name, when the full name is uncertain."},
            "last_name": {"type": "string", "description": "Last name."},
            "linkedin_url": {"type": "string", "description": "Profile URL — the most reliable single input."},
        },
        "required": [],
    },
}

JOBS_SALARY = {
    "name": "jobs_salary",
    "description": (
        "Real pay for a role in a place, from jobs that publish pay: median, p25/p50/p75/p90, a histogram, "
        "sample size and the share of postings that disclose salary. $0.01 per call. Citations come from "
        "the provider's own index, so state the sample size when you quote a number."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Job title, e.g. 'backend engineer'."},
            "city": {"type": "string", "description": "City, full name ('San Francisco')."},
            "region": {"type": "string", "description": "Region/state ('California')."},
            "country": {"type": "string", "description": "Country, full name ('United States')."},
            "days": {"type": "integer", "description": "Window in days (default 180, max 365)."},
        },
        "required": ["title"],
    },
}

JOBS_CHANGES = {
    "name": "jobs_changes",
    "description": (
        "Roles that closed since a date — the other half of a repeat check, and free of charge. Combine "
        "with jobs_search(days=…) to answer 'what is new and what is gone'. The date must be within the "
        "last 14 days."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since": {"type": "string", "description": "YYYY-MM-DD or an ISO datetime inside the last 14 days."},
            "limit": {"type": "integer", "description": "Rows (default 100, max 1000)."},
        },
        "required": ["since"],
    },
}

JOBS_TRACKER = {
    "name": "jobs_tracker",
    "description": (
        "The local pipeline: save a role, move it, note what you found, record who to write to. Actions: "
        "list, save, status, note, profile_get, profile_set, spend. Nothing here touches the network — the "
        "tracker is a local SQLite file. 'spend' reports what treg calls have cost so far."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "save", "status", "note", "profile_get", "profile_set", "spend"]},
            "id": {"type": "string", "description": "Job id (save/status/note)."},
            "status": {"type": "string", "description": "One of: saved, researching, applied, interview, offer, rejected, skipped."},
            "note": {"type": "string", "description": "Free-text note."},
            "person": {"type": "string", "description": "Who owns the role — name and/or email you verified."},
            "profile": {"type": "object", "description": PROFILE_DOC},
            "filter_status": {"type": "string", "description": "list: only this status."},
            "reset_spend": {"type": "boolean", "description": "spend: zero the local ledger after a top-up."},
        },
        "required": ["action"],
    },
}

JOBS_DOCTOR = {
    "name": "jobs_doctor",
    "description": (
        "Check the treg wiring: is the team key present, which of the eight tool ids still resolve, and what "
        "has been spent. Probes cost nothing (each request may pay zero). Run this first when a call fails, "
        "and after treg renames a tool — it reports the real verdict per id instead of guessing."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}
