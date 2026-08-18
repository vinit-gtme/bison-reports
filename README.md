# Bison Weekly Bounce & Reply Rate Reports

Automated weekly reports on cold email deliverability (bounce rate) and engagement (reply rate), pulled from the Bison cold email platform, aggregated to the domain level, emailed via Resend, and synced to Supabase for dashboarding in Grafana.

## What this does

Every Friday at 8PM ET, two GitHub Actions workflows run automatically:

1. **Bounce Rate Report** — fetches Sent/Bounced counts per mailbox, aggregates by sending domain, emails a CSV + 3 data-driven insights via Resend, and syncs domain-level results to Supabase.
2. **Reply Rate Report** — same pipeline, tracking Sent/Replied instead.

Both reports write into the same Supabase table (`domain_weekly_stats`), which powers a Grafana dashboard for trend visualization over time.

## Architecture

Bison's API has no single "domain-level stats" endpoint, so the pipeline is:

1. `GET /api/sender-emails` (paginated) → list every mailbox, extract domain from each mailbox's email address
2. `GET /api/campaign-events/stats` per mailbox → date-bucketed Sent/Bounced/Replied series for the report's date range
3. Aggregate mailbox-level results up to domain level → compute `bounce_rate = Bounced/Sent` and `reply_rate = Replied/Sent`
4. Email the results (CSV attachments + insights) via Resend
5. Upsert domain-level weekly totals into Supabase (`domain_weekly_stats`)

## Repo structure

- `bison_common.py` — shared API client, report builder, insights, Resend sender, Supabase sync
- `bounce_report.py` — Bounced/Sent report ("lower is better")
- `reply_rate_report.py` — Replied/Sent report ("higher is better")
- `requirements.txt` — requests, pandas, tqdm
- `schema.sql` — one-time Supabase table setup (run manually in SQL editor)
- `.github/workflows/bounce-report.yml`
- `.github/workflows/reply-rate-report.yml`

> **Important:** workflow files must live at exactly `.github/workflows/*.yml` — GitHub Actions won't detect them anywhere else in the repo (including the repo root).

## Required GitHub repo secrets

Settings → Secrets and variables → Actions → **Repository secrets**:

| Secret | Description |
|---|---|
| `BISON_BASE_URL` | e.g. `https://send.highticket.agency` |
| `BISON_API_TOKEN` | Bison bearer token |
| `RESEND_API_KEY` | Resend API key |
| `RESEND_FROM_EMAIL` | must be on a Resend-verified domain |
| `RESEND_TO_EMAIL` | recipient(s), comma-separated |
| `SUPABASE_URL` | Project URL, e.g. `https://xxxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service_role (or `sb_secret_...`) key — **never** the anon/publishable key |

## Supabase setup

1. Run `schema.sql` once in the Supabase SQL editor to create `domain_weekly_stats`.
2. The table has a unique constraint on `(domain, week_start)` — both workflows upsert into it using `on_conflict=domain,week_start`, so bounce and reply data merge into the same row per domain/week instead of overwriting or duplicating.

## Grafana dashboard

Connect Grafana Cloud (free tier) to Supabase via the **Transaction pooler** connection string (Project → Connect → Transaction pooler), not the direct connection. The password is your database password (not an API key), set under Database → Settings.

Current dashboard panels:
- Overall bounce rate / reply rate (this week) — Stat panels with red/green thresholds at 2% / 10%
- Company-wide bounce & reply rate trend over time
- "Needs attention" table — domains above the 2% bounce threshold this week
- Per-domain drill-down (optional, filterable by a `domain` dashboard variable)

## Workflow schedule & manual runs

- Two `cron` triggers (`0 1 * * SAT` / `0 0 * * SAT`) handle EST/EDT so the report fires at 8PM ET year-round; a guard step ensures only one actually runs per week.
- `workflow_dispatch` allows manual runs any time from the Actions tab — useful for testing.
- `REPORT_START_DATE` / `REPORT_END_DATE` env vars can override the default rolling Saturday–Friday window for ad-hoc date ranges.

## Known caveats

- GitHub auto-disables scheduled workflows after 60 days of no repo commit activity. A banner appears in the Actions tab if this happens — one click re-enables.
- The Supabase sync is wrapped in try/except in `bison_common.py`; a Supabase outage or misconfiguration prints a warning but never blocks the CSV/email send.
- Trend charts need at least 2-3 weeks of accumulated data before they show a meaningful line rather than a single point.

## Troubleshooting

- **"Supabase env vars not set" even after adding secrets** — check that the workflow `.yml` files actually live at `.github/workflows/`, not the repo root. Files elsewhere are silently ignored by GitHub Actions even though they show up fine in the repo's commit history.
- **`duplicate key value violates unique constraint` (Postgres error 23505)** — the Supabase upsert request must include `on_conflict=domain,week_start` as a query parameter. Without it, PostgREST's `Prefer: resolution=merge-duplicates` header only dedupes against the primary key (`id`), which never conflicts, so it falls through to a plain insert and fails on the real constraint.

## Local / manual testing

```bash
pip install -r requirements.txt
export BISON_BASE_URL=... BISON_API_TOKEN=... RESEND_API_KEY=... RESEND_FROM_EMAIL=... RESEND_TO_EMAIL=...
export SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=...
python bounce_report.py
python reply_rate_report.py
```
