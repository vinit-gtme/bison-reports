"""
Bison API — Domain & Mailbox level Bounce Rate report.
Fetches Sent/Bounced counts per mailbox, aggregates by domain, generates
3 CEO-ready insights, and emails everything via Resend.
"""

import os
import sys

import pandas as pd

from bison_common import (
    BisonClient,
    fetch_all_mailbox_metrics,
    build_mailbox_and_domain_reports,
    generate_insights,
    send_resend_email,
    insights_to_html,
    get_weekly_range,
)

# ---------- CONFIG (all from environment / GitHub Actions secrets) ----------
BASE_URL = os.environ["BISON_BASE_URL"]
API_TOKEN = os.environ["BISON_API_TOKEN"]

# Defaults to the rolling Saturday-Friday week ending on the day this runs.
# Override with REPORT_START_DATE / REPORT_END_DATE env vars for ad-hoc ranges.
_default_start, _default_end = get_weekly_range()
START_DATE = os.environ.get("REPORT_START_DATE", _default_start)
END_DATE = os.environ.get("REPORT_END_DATE", _default_end)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL")
RESEND_TO_EMAIL = os.environ.get("RESEND_TO_EMAIL")  # comma-separated if multiple

MAILBOX_CSV = "bounce_rate_by_mailbox.csv"
DOMAIN_CSV = "bounce_rate_by_domain.csv"
FAILED_CSV = "bounce_failed_mailboxes.csv"


def main():
    print(f"Bounce rate report window: {START_DATE} to {END_DATE}")
    client = BisonClient(BASE_URL, API_TOKEN)

    print("Fetching all mailboxes...")
    mailboxes = client.get_all_sender_emails()
    print(f"Unique mailboxes: {len(mailboxes)}")

    print(f"Fetching Sent/Bounced stats for {len(mailboxes)} mailboxes...")
    records, failed = fetch_all_mailbox_metrics(
        client, mailboxes, START_DATE, END_DATE,
        numerator_label="Bounced", denominator_label="Sent",
        checkpoint_path="_checkpoint_bounce.csv",
    )
    print(f"Collected {len(records)} mailbox-month rows.")

    if failed:
        print(f"!! {len(failed)} mailboxes failed after retries.")
        pd.DataFrame(failed).to_csv(FAILED_CSV, index=False)

    mailbox_report, domain_report, month_cols = build_mailbox_and_domain_reports(
        records, numerator_col_name="bounced", rate_col_name="bounce_rate_%"
    )

    if domain_report.empty:
        print("No data returned — check BISON_API_TOKEN, BISON_BASE_URL, and date range.")
        sys.exit(1)

    mailbox_report.to_csv(MAILBOX_CSV, index=False)
    domain_report.to_csv(DOMAIN_CSV, index=False)
    print(f"Saved {MAILBOX_CSV} ({len(mailbox_report)} mailboxes)")
    print(f"Saved {DOMAIN_CSV} ({len(domain_report)} domains)")

    insights = generate_insights(
        domain_report, month_cols,
        numerator_col_name="bounced", rate_col_name="bounce_rate_%",
        metric_label="bounce rate", good_direction="low", healthy_threshold=2.0,
    )
    print("\nInsights:")
    for i in insights:
        print(f" - {i}")

    if RESEND_API_KEY and RESEND_FROM_EMAIL and RESEND_TO_EMAIL:
        html = f"""
        <div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">
          <h2>Weekly Bounce Rate Report</h2>
          <p>Period: {START_DATE} to {END_DATE}</p>
          {insights_to_html(insights, "Top 3 Insights")}
          <p style="margin-top:16px;">Full mailbox- and domain-level breakdowns are attached as CSV.</p>
        </div>
        """
        to_list = [e.strip() for e in RESEND_TO_EMAIL.split(",") if e.strip()]
        send_resend_email(
            RESEND_API_KEY, RESEND_FROM_EMAIL, to_list,
            subject=f"Bounce Rate Report ({START_DATE} to {END_DATE})",
            html_body=html,
            attachment_paths=[MAILBOX_CSV, DOMAIN_CSV],
        )
        print("Email sent via Resend.")
    else:
        print("Resend env vars not fully set — skipping email send.")


if __name__ == "__main__":
    main()
