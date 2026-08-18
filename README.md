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
