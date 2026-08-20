# Bison Weekly Bounce & Reply Rate Reports + Infrastructure Health Dashboard

Automated weekly reports on cold email deliverability (bounce rate) and engagement (reply rate), pulled from the Bison cold email platform, aggregated at both the domain and mailbox level, emailed via Resend, synced to Supabase, and visualized in two lightweight dashboards hosted on GitHub Pages.

## What this does

Every Friday at 8PM ET, two GitHub Actions workflows run automatically:

1. **Bounce Rate Report** — fetches Sent/Bounced counts per mailbox, aggregates by domain and by mailbox, emails a CSV + 3 data-driven insights via Resend, and syncs both levels to Supabase.
2. **Reply Rate Report** — same pipeline, tracking Sent/Replied instead.

Both reports write into two Supabase tables (`domain_weekly_stats` and `mailbox_weekly_stats`), which power two dashboards:

- **`docs/index.html`** — "Infrastructure Health Dashboard": a simple, glance-and-go overview. Two big status numbers (color-coded red/green/yellow), a company-wide trend chart, and a "needs attention" list. Built for a non-technical viewer (e.g. a CEO) — nothing to click or configure.
- **`docs/explorer.html`** — a full filterable explorer: date range, domain/mailbox search, numeric range filters, a sortable table, CSV export, and a Domain/Mailbox toggle for switching granularity.

Both are static pages hosted free via GitHub Pages, reading directly from Supabase with a read-only anon key, no server to run or maintain.

## Architecture

Bison's API has no single "domain-level stats" endpoint, so the pipeline is:

1. `GET /api/sender-emails` (paginated) → list every mailbox, extract domain from each mailbox's email address
2. `GET /api/campaign-events/stats` per mailbox → date-bucketed Sent/Bounced/Replied series for the report's date range (all three metrics come back in a single call, regardless of which one you're targeting)
3. Aggregate mailbox-level results up to domain level → compute `bounce_rate = Bounced/Sent` and `reply_rate = Replied/Sent` at both levels
4. Email the results (CSV attachments + insights) via Resend
5. Upsert both domain-level and mailbox-level weekly totals into Supabase

## Repo structure

- `bison_common.py` — shared API client, report builder, insights, Resend sender, Supabase sync (domain + mailbox level)
- `bounce_report.py` — Bounced/Sent report ("lower is better")
- `reply_rate_report.py` — Replied/Sent report ("higher is better")
- `backfill_weekly_history.py` — standalone, one-time (or occasional) script to backfill historical weekly domain- and mailbox-level stats from a fixed start date; run manually (e.g. in Google Colab), not on a schedule
- `requirements.txt` — requests, pandas, tqdm
- `schema.sql` — one-time Supabase table setup (run manually in SQL editor)
- `.github/workflows/bounce-report.yml`
- `.github/workflows/reply-rate-report.yml`
- `docs/index.html` — simple executive dashboard (GitHub Pages entry point)
- `docs/explorer.html` — filterable analyst dashboard

> **Important:** workflow files must live at exactly `.github/workflows/*.yml`, GitHub Actions won't detect them anywhere else in the repo (including the repo root). Similarly, GitHub Pages must be configured to serve from `/docs` on the `main` branch (Settings → Pages).

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
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service_role (or `sb_secret_...`) key, **never** the anon/publishable key |

## Supabase setup

1. Run `schema.sql` once in the Supabase SQL editor to create both `domain_weekly_stats` and `mailbox_weekly_stats`.
2. Both tables upsert on a unique constraint (`domain, week_start` / `mailbox_id, week_start`) using `on_conflict`, required because PostgREST's `Prefer: resolution=merge-duplicates` only dedupes against the primary key by default, which never conflicts on its own.
3. **Row Level Security (RLS):** both tables need RLS enabled with a `SELECT`-only policy for the `anon` role (Table Editor → table → RLS → New Policy → Command: `SELECT`, Target Roles: `anon`, Using expression: `true`). This lets the two HTML dashboards read data safely with the anon key, while the GitHub Actions writes (which use the `service_role` key) always bypass RLS entirely and are unaffected either way.
4. `bounce_rate` and `reply_rate` are **generated columns**, never include them in an insert/upsert payload; Postgres computes them automatically from `sent`/`bounced`/`replied`.

## Dashboards (GitHub Pages)

1. Put `index.html` and `explorer.html` inside a `docs/` folder in the repo.
2. In each file, fill in `SUPABASE_URL` and `SUPABASE_ANON_KEY` at the top of the script block (Project Settings → API → the **anon/public** key, never service_role, since these files run client-side in a browser).
3. Repo → Settings → Pages → Source: Deploy from branch → `main` → folder `/docs` → Save.
4. GitHub gives you a URL like `https://<username>.github.io/<repo>/`, that serves `index.html` by default; `explorer.html` needs to be typed explicitly (`.../explorer.html`) or reached via the link on the overview page.
5. **Note:** GitHub Pages requires a **public** repo on the Free plan; private repos need a paid plan to publish Pages.

Both dashboards page through Supabase's 1000-row-per-request cap automatically (fetching the first page with an exact count, then firing all remaining pages in parallel), so they load the full table regardless of size.

### Status coloring (both dashboards)

- **Bounce:** red if the rate is above 2% **or** worse than last week; green otherwise. (No yellow state, the red condition's logical opposite fully covers green.)
- **Reply:** red if below 1% **and** worse than last week; green if at/above 1% **and** better than last week; yellow for everything else (e.g. healthy but flat, or unhealthy but improving).
- These thresholds are set via `BOUNCE_THRESHOLD` / `REPLY_THRESHOLD` constants near the top of each file's script block.

## Workflow schedule & manual runs

- Two `cron` triggers (`0 1 * * SAT` / `0 0 * * SAT`) handle EST/EDT so the report fires at 8PM ET year-round; a guard step ensures only one actually runs per week.
- `workflow_dispatch` allows manual runs any time from the Actions tab, useful for testing.
- `REPORT_START_DATE` / `REPORT_END_DATE` env vars can override the default rolling Saturday–Friday window for ad-hoc date ranges.

## Backfilling historical data

`backfill_weekly_history.py` is a standalone script (not part of the scheduled pipeline) for populating history before the automated sync started. Run it manually, e.g. in Google Colab:

```bash
export BISON_BASE_URL=...
export BISON_API_TOKEN=...
export SUPABASE_URL=...
export SUPABASE_SERVICE_ROLE_KEY=...
export BACKFILL_START_DATE=2026-01-01   # optional, this is the default
export SKIP_DOMAIN_UPLOAD=true          # optional, skip re-uploading domain_weekly_stats
                                          # if it's already backfilled and you only need mailbox data

python backfill_weekly_history.py
```

It fetches each mailbox's full day-level history in a single API call per mailbox (not per week), buckets it into the same Saturday-Friday weeks the regular reports use, and upserts into both Supabase tables, safe to re-run any time since everything merges on the same conflict keys as the live weekly sync.

## Known caveats

- GitHub auto-disables scheduled workflows after 60 days of no repo commit activity. A banner appears in the Actions tab if this happens, one click re-enables.
- The Supabase sync (in both the weekly reports and the backfill script) is wrapped in try/except; a Supabase outage or misconfiguration prints a warning but never blocks the CSV/email send.
- Trend charts need at least 2-3 weeks of accumulated data before they show a meaningful line rather than a single point.
- Status coloring's "vs. last week" comparison has no prior week to check in the very first week or two after go-live; bounce falls back to pure threshold, reply defaults to yellow until there's a real trend to compare against.

## Troubleshooting

- **"Supabase env vars not set" even after adding secrets** — check that the workflow `.yml` files actually live at `.github/workflows/`, not the repo root. Files elsewhere are silently ignored by GitHub Actions even though they show up fine in the repo's commit history.
- **`duplicate key value violates unique constraint` (Postgres error 23505)** — the Supabase upsert request must include `on_conflict=domain,week_start` (or `mailbox_id,week_start`) as a query parameter. Without it, PostgREST's `Prefer: resolution=merge-duplicates` header only dedupes against the primary key (`id`), which never conflicts, so it falls through to a plain insert and fails on the real constraint.
- **`cannot insert a non-DEFAULT value into column "bounce_rate"` (Postgres error 428C9)** — `bounce_rate`/`reply_rate` are generated columns; drop them from the payload before uploading and let Postgres compute them.
- **Dashboard shows fewer rows/weeks than expected** — Supabase caps a single REST request at 1000 rows. Both dashboards page through this automatically; if you're writing a new query against these tables elsewhere, remember to paginate.
- **GitHub Pages shows no URL** — confirm the repo is public (Pages requires a paid plan for private repos on the Free tier), and confirm `docs/index.html` actually exists in the repo before checking the Pages settings.

## Local / manual testing

```bash
pip install -r requirements.txt
export BISON_BASE_URL=... BISON_API_TOKEN=... RESEND_API_KEY=... RESEND_FROM_EMAIL=... RESEND_TO_EMAIL=...
export SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=...
python bounce_report.py
python reply_rate_report.py
```

## Planned: mailbox sending-limit throttle (not yet implemented)

Design in progress: automatically reduce a mailbox's `daily_limit` (via Bison's `PATCH /api/sender-emails/daily-limits/bulk`) when its weekly bounce rate hits 2%+, then check the following week and restore the original limit if bounce rate recovers below 1%. Will include a `mailbox_throttle_log` audit table and a dashboard section showing trigger week, trigger bounce rate, limit change, and recovery status per mailbox. Open questions before implementation: exact limit-reduction mapping, whether recovery-checking repeats indefinitely or escalates/caps after N weeks, and whether the first version runs live or in a dry-run/log-only mode.
