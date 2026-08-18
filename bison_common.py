"""
Shared utilities for Bison API reports (bounce rate, reply rate, etc.)
"""

import os
import time
import base64
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable


# -----------------------------------------------------------------------------
# Rolling weekly window: Saturday through Friday, ending on "today" (or the
# most recent Friday if run on an off day, e.g. during a manual test).
#
#   Last week:  Aug 8  - Aug 14
#   This week:  Aug 15 - Aug 21
#   Next week:  Aug 22 - Aug 28
#   Then:       Aug 29 - Sep 4
#
# Since the workflow fires every Friday 8PM ET, calling this with no args on
# that Friday gives start=previous Saturday, end=that same Friday.
# -----------------------------------------------------------------------------
def get_weekly_range(now=None):
    if now is None:
        now = datetime.now(ZoneInfo("America/New_York"))
    # Friday = weekday 4. Find the most recent Friday on or before "now".
    days_since_friday = (now.weekday() - 4) % 7
    friday = (now - timedelta(days=days_since_friday)).date()
    saturday = friday - timedelta(days=6)
    return saturday.isoformat(), friday.isoformat()


# -----------------------------------------------------------------------------
# Bison API client
# -----------------------------------------------------------------------------
class BisonClient:
    def __init__(self, base_url, api_token, max_retries=4):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
        }
        self.max_retries = max_retries
        self._local = threading.local()

    def _session(self):
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update(self.headers)
            self._local.session = s
        return self._local.session

    def get(self, path, params=None):
        url = f"{self.base_url}{path}"
        session = self._session()
        for attempt in range(1, self.max_retries + 1):
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"Failed after {self.max_retries} retries: {url}")

    def get_all_sender_emails(self):
        mailboxes = []
        page = 1
        while True:
            data = self.get("/api/sender-emails", params={"page": page})
            rows = data.get("data", [])
            mailboxes.extend(rows)

            meta = data.get("meta", {})
            current_page = meta.get("current_page", page)
            last_page = meta.get("last_page", page)
            print(f"  fetched sender-emails page {current_page}/{last_page} ({len(rows)} mailboxes)")

            if not rows or current_page >= last_page:
                break
            page += 1
            time.sleep(0.2)

        # de-dupe just in case
        seen, deduped = set(), []
        for m in mailboxes:
            if m.get("id") not in seen:
                seen.add(m.get("id"))
                deduped.append(m)
        return deduped

    def get_metric_stats_for_mailbox(self, sender_email_id, start_date, end_date,
                                      numerator_label, denominator_label="Sent"):
        """Returns {month_key: {'sent': n, 'numerator': n}} for one mailbox."""
        data = self.get("/api/campaign-events/stats", params={
            "start_date": start_date,
            "end_date": end_date,
            "sender_email_ids[]": sender_email_id,
        })
        monthly = defaultdict(lambda: {"sent": 0, "numerator": 0})
        for series in data.get("data", []):
            label = series.get("label", "")
            if label == denominator_label:
                key = "sent"
            elif label == numerator_label:
                key = "numerator"
            else:
                continue
            for date_str, count in series.get("dates", []):
                month_key = date_str[:7]
                monthly[month_key][key] += count
        return monthly


# -----------------------------------------------------------------------------
# Fetch metric for every mailbox concurrently
# -----------------------------------------------------------------------------
def fetch_all_mailbox_metrics(client, mailboxes, start_date, end_date,
                               numerator_label, denominator_label="Sent",
                               max_workers=10, checkpoint_path=None, checkpoint_every=50):
    records = []
    failed = []

    def fetch_one(mbox):
        mbox_id = mbox.get("id")
        email = mbox.get("email", "")
        domain = email.split("@")[-1].lower() if "@" in email else "unknown"
        monthly = client.get_metric_stats_for_mailbox(
            mbox_id, start_date, end_date, numerator_label, denominator_label
        )
        return mbox_id, email, domain, monthly

    counter = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, m): m for m in mailboxes}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Mailboxes processed"):
            mbox = futures[future]
            try:
                mbox_id, email, domain, monthly = future.result()
            except Exception as e:
                failed.append({"mailbox_id": mbox.get("id"), "email": mbox.get("email"), "error": str(e)})
                continue

            for month_key, counts in monthly.items():
                records.append({
                    "mailbox_id": mbox_id,
                    "email": email,
                    "domain": domain,
                    "month": month_key,
                    "sent": counts["sent"],
                    "numerator": counts["numerator"],
                })

            counter += 1
            if checkpoint_path and counter % checkpoint_every == 0:
                pd.DataFrame(records).to_csv(checkpoint_path, index=False)

    return records, failed


# -----------------------------------------------------------------------------
# Report building (generic — works for bounce rate, reply rate, etc.)
# -----------------------------------------------------------------------------
def rate(numerator, denominator):
    return round((numerator / denominator) * 100, 2) if denominator > 0 else 0.0


def build_mailbox_and_domain_reports(records, numerator_col_name, rate_col_name):
    """
    records: list of dicts with keys mailbox_id, email, domain, month, sent, numerator
    numerator_col_name: e.g. 'bounced' or 'replied' (used for CSV column names)
    rate_col_name: e.g. 'bounce_rate_%' or 'reply_rate_%'
    Returns (mailbox_report_df, domain_report_df, month_cols)
    """
    df = pd.DataFrame(records)
    if df.empty:
        return df, df, []

    df = df.rename(columns={"numerator": numerator_col_name})

    def _build(index_cols):
        pivot_sent = df.pivot_table(index=index_cols, columns="month", values="sent",
                                     aggfunc="sum", fill_value=0)
        pivot_num = df.pivot_table(index=index_cols, columns="month", values=numerator_col_name,
                                    aggfunc="sum", fill_value=0)
        month_cols = sorted(set(pivot_sent.columns) | set(pivot_num.columns))

        report = pivot_sent.copy()[[]].reset_index()
        for m in month_cols:
            s = pivot_sent[m] if m in pivot_sent.columns else 0
            n = pivot_num[m] if m in pivot_num.columns else 0
            report[f"{m}_sent"] = s.values if hasattr(s, "values") else s
            report[f"{m}_{numerator_col_name}"] = n.values if hasattr(n, "values") else n
            report[f"{m}_{rate_col_name}"] = [
                rate(a, b) for a, b in zip(report[f"{m}_{numerator_col_name}"], report[f"{m}_sent"])
            ]

        report["total_sent"] = sum(report[f"{m}_sent"] for m in month_cols)
        report[f"total_{numerator_col_name}"] = sum(report[f"{m}_{numerator_col_name}"] for m in month_cols)
        report[f"total_{rate_col_name}"] = [
            rate(a, b) for a, b in zip(report[f"total_{numerator_col_name}"], report["total_sent"])
        ]
        return report, month_cols

    mailbox_report, month_cols = _build(["mailbox_id", "email", "domain"])
    domain_report, _ = _build(["domain"])

    mailbox_counts = df.groupby("domain")["mailbox_id"].nunique().rename("mailbox_count").reset_index()
    domain_report = domain_report.merge(mailbox_counts, on="domain", how="left")

    mailbox_report = mailbox_report.sort_values(f"total_{rate_col_name}", ascending=False)
    domain_report = domain_report.sort_values(f"total_{rate_col_name}", ascending=False)

    return mailbox_report, domain_report, month_cols


# -----------------------------------------------------------------------------
# CEO-ready insights (3 per report, generated from the actual data)
# -----------------------------------------------------------------------------
def generate_insights(domain_report, month_cols, numerator_col_name, rate_col_name,
                       metric_label, good_direction="low", healthy_threshold=2.0):
    """
    good_direction: "low" (bounce rate — lower is better) or "high" (reply rate — higher is better)
    Returns a list of exactly 3 plain-English insight strings.
    """
    insights = []
    if domain_report.empty:
        return [f"No data available for {metric_label} in this period."] * 3

    total_sent = domain_report["total_sent"].sum()
    total_num = domain_report[f"total_{numerator_col_name}"].sum()
    overall_rate = rate(total_num, total_sent)
    insights.append(
        f"Overall {metric_label} across all domains is {overall_rate}% "
        f"({total_num:,} of {total_sent:,} emails sent)."
    )

    top_row = domain_report.iloc[0]  # already sorted descending by rate
    if good_direction == "low":
        insights.append(
            f"'{top_row['domain']}' has the highest {metric_label} at "
            f"{top_row[f'total_{rate_col_name}']}% across {int(top_row['total_sent']):,} sends — "
            f"worth investigating or pausing this domain."
        )
    else:
        insights.append(
            f"'{top_row['domain']}' is the top performer with a {metric_label} of "
            f"{top_row[f'total_{rate_col_name}']}% across {int(top_row['total_sent']):,} sends — "
            f"worth scaling sends here."
        )

    if len(month_cols) >= 2:
        first_m, last_m = month_cols[0], month_cols[-1]
        first_rate = rate(domain_report[f"{first_m}_{numerator_col_name}"].sum(),
                           domain_report[f"{first_m}_sent"].sum())
        last_rate = rate(domain_report[f"{last_m}_{numerator_col_name}"].sum(),
                          domain_report[f"{last_m}_sent"].sum())
        direction = "up" if last_rate > first_rate else "down" if last_rate < first_rate else "flat"
        insights.append(
            f"{metric_label.capitalize()} moved {direction}, from {first_rate}% in {first_m} "
            f"to {last_rate}% in {last_m}."
        )
    else:
        if good_direction == "low":
            count = int((domain_report[f"total_{rate_col_name}"] > healthy_threshold).sum())
            insights.append(
                f"{count} of {len(domain_report)} domains are above the {healthy_threshold}% "
                f"healthy {metric_label} threshold."
            )
        else:
            count = int((domain_report[f"total_{rate_col_name}"] < healthy_threshold).sum())
            insights.append(
                f"{count} of {len(domain_report)} domains are below the {healthy_threshold}% "
                f"{metric_label} benchmark — potential opportunity to improve targeting or copy."
            )

    return insights[:3]


# -----------------------------------------------------------------------------
# Resend email delivery
# -----------------------------------------------------------------------------
def send_resend_email(api_key, from_email, to_emails, subject, html_body, attachment_paths=None):
    attachments = []
    for path in (attachment_paths or []):
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")
        attachments.append({"filename": os.path.basename(path), "content": content})

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": from_email,
            "to": to_emails if isinstance(to_emails, list) else [to_emails],
            "subject": subject,
            "html": html_body,
            "attachments": attachments,
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"Resend API error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.json()


def insights_to_html(insights, title):
    items = "".join(f"<li style='margin-bottom:8px;'>{i}</li>" for i in insights)
    return f"""
    <h3 style="margin-bottom:6px;">{title}</h3>
    <ul style="padding-left:20px;">{items}</ul>
    """


# -----------------------------------------------------------------------------
# Supabase sync — one weekly snapshot row per domain, upserted by (domain, week_start).
#
# Called separately by bounce_report.py (writes sent + bounced) and
# reply_rate_report.py (writes sent + replied). Each call only includes the
# columns it actually has data for, so PostgREST's merge-duplicates upsert
# updates just those columns and leaves the other script's columns alone —
# no risk of one report blanking out the other's numbers.
#
# Wrapped in try/except: if Supabase is unreachable or misconfigured, this
# prints a warning and returns quietly. It never raises, so it can never
# break the existing CSV/email flow.
# -----------------------------------------------------------------------------
def push_domain_stats_to_supabase(domain_report, week_start, week_end, numerator_col_name):
    """
    domain_report: the domain-level DataFrame from build_mailbox_and_domain_reports()
    numerator_col_name: 'bounced' or 'replied' — must match total_{numerator_col_name}
                         column already present in domain_report
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        print("Supabase env vars not set — skipping Supabase sync.")
        return

    if domain_report.empty:
        print("Domain report empty — nothing to sync to Supabase.")
        return

    payload = []
    for _, row in domain_report.iterrows():
        payload.append({
            "domain": row["domain"],
            "week_start": week_start,
            "week_end": week_end,
            "sent": int(row["total_sent"]),
            numerator_col_name: int(row[f"total_{numerator_col_name}"]),
        })

    try:
        resp = requests.post(
            f"{supabase_url.rstrip('/')}/rest/v1/domain_weekly_stats",
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 300:
            print(f"Supabase sync error {resp.status_code}: {resp.text}")
        else:
            print(f"Synced {len(payload)} domain rows to Supabase ({numerator_col_name}).")
    except Exception as e:
        # Never let a Supabase hiccup break the report/email run.
        print(f"Supabase sync failed (non-fatal, email will still send): {e}")
