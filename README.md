# treg-jobs

The whole job hunt, run by your agent through [treg](https://treg.to)'s job API: find roles across
companies, rank them against a profile, find the people who work there, get one person's work email,
check what the role actually pays, and keep a local pipeline of what you sent and what came back.

It never applies for you. No application is submitted, no email is sent, no job board is logged in
to — the agent prepares, you pull the trigger.

## What it costs

Every network call is one metered request to `treg.to/call/<id>`, billed to your own treg team
balance at the provider's rate with 0% markup. New teams start with $1.00 of credit.

| Tool | What it does | Observed cost |
|---|---|---|
| `jobs_search` | roles across companies, by title / employer / domain / country / remote | ~$0.003 per call (billed per returned job) |
| `jobs_match` | semantic fit search against a profile | ~$0.0045 for 3 roles, 15–25 s |
| `jobs_people` | who works at that company, with titles and LinkedIn URLs | ~$0.007 per call |
| `jobs_email` | one person's work email | ~$0.001–0.005 per call |
| `jobs_salary` | median / p25 / p90, sample size, share that discloses pay | $0.01 per call |
| `jobs_changes` | roles that closed since a date | **free** |
| `jobs_tracker` | local pipeline + spend ledger | free, no network |
| `jobs_doctor` | wiring check: key, tool ids, spend | **free** (probes carry a zero cost cap) |

Two limits stop a runaway bill: `max_cost_usd` is sent on every request as
`X-Treg-Route-Max-Cost`, and `budget_usd` is a local session ceiling — once the ledger reaches it,
calls are refused before they are made. Every result reports its own cost and the provider that
served it.

## Install

```bash
hermes plugins install YAN-XBT/treg-jobs      # or: copy this directory to $HERMES_HOME/plugins/treg-jobs
```

Set the team key (one key carries the team and its balance). The key is entered through a hidden
prompt and never passes through a chat:

```bash
python "$HERMES_HOME/skills/autonomous-ai-agents/hermes-agent/scripts/set_env_secret.py" TREG_TOKEN \
  --hint "treg dashboard → Your API key"
```

The plugin reads `TREG_TOKEN` with `agent.secret_scope.get_secret`, the route that honours the active
profile — it never parses `.env` or any other credential file, so a gateway serving several profiles
cannot hand it a sibling's key.

No other dependencies: no CLI, no Node, no provider accounts. `python_dependencies: []`.

## Use

```
jobs_search(query="backend engineer", country="Germany", days=30, limit=10)
jobs_match(profile={summary:"8 years python backend, distributed systems, remote EU"})
jobs_people(company="<job id>")                    # employer domain, or the company name when the board hides it
jobs_email(linkedin_url="https://linkedin.com/in/…")
jobs_salary(title="backend engineer", country="Germany")
jobs_changes(since="2026-11-13")
jobs_tracker(action="status", id="…", status="applied", person="…")
```

Roles are cached in a local SQLite file, so the second look at the same search is free: `jobs_search`
reports `new_since_last_seen`, and the tracker keeps the pipeline that a chat scrolls past.

## What it will not do

- It does not verify an address it finds. A hit is the provider's best match, labelled as such;
  treat it as a candidate, not a fact.
- It does not pick the hiring manager for you. `jobs_people` returns the profiles a provider lists at
  that company, with titles and tenure — read them rather than trusting the first row.
- It does not quote a salary without its basis. `jobs_salary` returns the sample size and the share
  of postings that disclose pay, because a median over 31 rows is not a market.

## Disclosure

- **Network:** HTTPS requests to `https://treg.to` only, one per tool call, carrying the team key in
  `X-Treg-Token`, the call's cost ceiling, and optionally `X-Treg-Route-Waterfall`. treg reaches the
  underlying providers with its own credentials. No provider account, key or cookie lives here.
- **Credentials:** `TREG_TOKEN` is resolved through Hermes's secret scope at call time. The plugin
  never reads a `.env`, `auth.json`, keychain or vendor token file; it never stores the key, never
  logs it, and never includes it in a result or an error message.
- **Storage:** one SQLite file in the plugin data directory — cached postings, the pipeline, the
  profile, and the spend ledger. Nothing else is written.
- **No telemetry, no shell, no background process, nothing applied to.**

## Tests

```bash
python tests/test_treg_jobs.py     # 12 checks, offline, no key, no network
hermes plugins validate .            # catalog gate
hermes plugins doctor . --ci         # real discovery, import and registration
```

The suite swaps the treg client for fixtures, so every handler path — including the error envelopes —
runs without spending anything.

## Layout

```
plugin.yaml   manifest, key requirement, settings
__init__.py   register(ctx): eight tools
treg.py       HTTP client, key candidates, cost ledger, error mapping
tools.py      the eight handlers
schemas.py    tool schemas
store.py      SQLite: postings, tracker, profile, ledger
tests/        offline suite
```

The tool ids in `treg.py` were verified against the live catalog, not copied from a landing page: two
ids advertised there (`people.search`, `people.email.find`) do not resolve, and the routed
`treg.people.*` ones do. `jobs_doctor` re-checks them at any time, and `tool_map` in the settings
overrides any id that moves.
