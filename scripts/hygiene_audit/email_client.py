# =============================================================================
# email_client.py — SendGrid email notifications
# Sends per-rep HTML audit emails with Ari, Blair, Imtiaz CC'd.
# From: harishnath@amzprep.com (swap to reports@amzprep.com for production)
# =============================================================================

import os
import time
import sendgrid
from sendgrid.helpers.mail import Mail, To, Cc, From, Content
from datetime import datetime, timedelta, timezone
from config import ARI, EMAIL_CC, EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME, REPS, IS_DEV, resolve_email, message_prefix


MAX_PAST_DUE_SHOWN = 10
MAX_STALE_SHOWN    = 10
MAX_CONTACTS_SHOWN = 10


# -----------------------------------------------------------------------------
# Date helper
# -----------------------------------------------------------------------------

def _week_label() -> str:
    today = datetime.now(tz=timezone.utc)
    days_since_monday = today.weekday()
    last_monday = today - timedelta(days=days_since_monday + 7)
    last_sunday  = last_monday + timedelta(days=6)
    if last_monday.month == last_sunday.month:
        return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%-d, %Y')}"
    return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%B %-d, %Y')}"


# -----------------------------------------------------------------------------
# HTML email body builder
# -----------------------------------------------------------------------------

STYLE = """
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
         font-size: 14px; color: #1a1a1a; background: #f5f5f5; margin: 0; padding: 0; }
  .wrapper { max-width: 680px; margin: 24px auto; background: #ffffff;
             border-radius: 8px; overflow: hidden;
             border: 1px solid #e0e0e0; }
  .header { background: #0b1829; color: #ffffff; padding: 24px 32px; }
  .header h1 { margin: 0 0 4px; font-size: 18px; font-weight: 600; }
  .header p  { margin: 0; font-size: 13px; color: #9ab0c8; }
  .body { padding: 24px 32px; }
  .summary-table { width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 13px; }
  .summary-table th { background: #f0f0f0; text-align: left; padding: 8px 12px;
                      font-weight: 600; border-bottom: 2px solid #ddd; }
  .summary-table td { padding: 8px 12px; border-bottom: 1px solid #f0f0f0; }
  .summary-table tr:last-child td { border-bottom: none; }
  .count { font-weight: 700; }
  .count.red   { color: #c0392b; }
  .count.amber { color: #e67e22; }
  .count.green { color: #27ae60; }
  .section-title { font-size: 13px; font-weight: 700; color: #0b1829;
                   text-transform: uppercase; letter-spacing: 0.5px;
                   margin: 24px 0 8px; border-top: 1px solid #e8e8e8; padding-top: 16px; }
  .deal-list { list-style: none; margin: 0; padding: 0; }
  .deal-list li { padding: 6px 0; border-bottom: 1px solid #f5f5f5; font-size: 13px; }
  .deal-list li:last-child { border-bottom: none; }
  .deal-list a { color: #1a73e8; text-decoration: none; font-weight: 500; }
  .deal-list a:hover { text-decoration: underline; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
           font-size: 11px; font-weight: 600; margin-left: 6px; }
  .badge.red   { background: #fdecea; color: #c0392b; }
  .badge.amber { background: #fef9e7; color: #b7770d; }
  .more { font-size: 12px; color: #888; font-style: italic; margin: 4px 0 0; }
  .actions { background: #f8f9fa; border-radius: 6px; padding: 16px 20px; margin: 20px 0 0; }
  .actions h3 { margin: 0 0 10px; font-size: 13px; font-weight: 700; color: #0b1829; }
  .actions ol  { margin: 0; padding-left: 18px; font-size: 13px; color: #333; }
  .actions li  { margin-bottom: 6px; }
  .footer { background: #f5f5f5; padding: 16px 32px; font-size: 11px; color: #888;
            border-top: 1px solid #e0e0e0; }
  .ff-ok   { color: #27ae60; font-weight: 600; }
  .ff-warn { color: #e67e22; font-weight: 600; }
</style>
"""


def _count_class(n: int, warn: int = 1, critical: int = 10) -> str:
    if n == 0:
        return "green"
    if n < critical:
        return "amber"
    return "red"


def _build_html_body(rep: dict, data: dict, week_label: str, ff_data: dict) -> str:
    oid        = rep["owner_id"]
    ff         = ff_data.get(oid, {"count": 0, "status": "NO DATA"})
    open_count = data["open_deals"]

    def count_badge(n: int) -> str:
        cls = _count_class(n)
        return f'<span class="count {cls}">{n}</span>'

    summary_rows = [
        ("Past-due close date",      len(data["past_due"])),
        ("Stale (14d+ no activity)", len(data["stale"])),
        ("Missing deal amount",      len(data["missing_amount"])),
        ("Missing pipeline source",  len(data["missing_source"])),
        ("Missing MRR",              len(data["missing_mrr"])),
        ("Missing deal status",      len(data["missing_status"])),
        ("Missing lead status",      len(data["missing_lead_status"])),
    ]

    summary_html = "".join(
        f"<tr><td>{label}</td><td>{count_badge(n)}</td></tr>"
        for label, n in summary_rows
    )

    # Past-due deals section
    past_due_html = ""
    if data["past_due"]:
        items = ""
        for d in data["past_due"][:MAX_PAST_DUE_SHOWN]:
            date_str = f"due {d['close_date_str']}" if d["close_date_str"] else "no close date set"
            items += f'<li><a href="{d["url"]}">{d["name"]}</a> <span class="badge red">{date_str}</span></li>'
        overflow = ""
        if len(data["past_due"]) > MAX_PAST_DUE_SHOWN:
            overflow = f'<p class="more">...and {len(data["past_due"]) - MAX_PAST_DUE_SHOWN} more</p>'
        past_due_html = f"""
        <div class="section-title">Top past-due deals (oldest first)</div>
        <ul class="deal-list">{items}</ul>{overflow}"""

    # Stale deals section
    stale_html = ""
    if data["stale"]:
        items = ""
        for d in data["stale"][:MAX_STALE_SHOWN]:
            if d["days_inactive"] is None:
                activity_str = "no activity ever"
            else:
                activity_str = f"{d['days_inactive']}d inactive"
            items += f'<li><a href="{d["url"]}">{d["name"]}</a> <span class="badge amber">{activity_str}</span></li>'
        overflow = ""
        if len(data["stale"]) > MAX_STALE_SHOWN:
            overflow = f'<p class="more">...and {len(data["stale"]) - MAX_STALE_SHOWN} more</p>'
        stale_html = f"""
        <div class="section-title">Top stale deals (worst first)</div>
        <ul class="deal-list">{items}</ul>{overflow}"""

    # Contacts missing lead status
    contacts_html = ""
    if data["missing_lead_status"]:
        items = "".join(
            f'<li><a href="{c["url"]}">{c["name"]}</a></li>'
            for c in data["missing_lead_status"][:MAX_CONTACTS_SHOWN]
        )
        overflow = ""
        if len(data["missing_lead_status"]) > MAX_CONTACTS_SHOWN:
            overflow = f'<p class="more">...and {len(data["missing_lead_status"]) - MAX_CONTACTS_SHOWN} more</p>'
        contacts_html = f"""
        <div class="section-title">Contacts missing lead status</div>
        <ul class="deal-list">{items}</ul>{overflow}"""

    # Fireflies
    if ff["status"] == "OK":
        ff_html = f'<p class="ff-ok">&#10003; {ff["count"]} Fireflies transcript(s) recorded last week</p>'
    else:
        ff_html = f'<p class="ff-warn">&#9888; No Fireflies transcripts found last week. Ensure Fireflies is connected to your calendar.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">{STYLE}</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>HubSpot Hygiene Audit — {rep['first_name']}</h1>
    <p>Week of {week_label} &nbsp;|&nbsp; {open_count} open deals</p>
  </div>
  <div class="body">
    <table class="summary-table">
      <thead><tr><th>Issue</th><th>Count</th></tr></thead>
      <tbody>{summary_html}</tbody>
    </table>

    {past_due_html}
    {stale_html}
    {contacts_html}

    <div class="section-title">Fireflies call tracking</div>
    {ff_html}

    <div class="actions">
      <h3>What to do</h3>
      <ol>
        <li>Update or close-lost any deals with past-due close dates</li>
        <li>Log activity or close-lost stale deals with no engagement</li>
        <li>Fill in Deal Amount, Pipeline Source, MRR, and Deal Status on every open deal</li>
        <li>Freight-only deals: set MRR to $0 explicitly</li>
        <li>Assign Lead Status to all contacts without one</li>
      </ol>
    </div>
  </div>
  <div class="footer">
    All unresolved issues carry forward every week until fixed. &nbsp;|&nbsp;
    Sent by AMZ Prep Hygiene Audit &nbsp;|&nbsp; amzprep.com
  </div>
</div>
</body>
</html>"""


# -----------------------------------------------------------------------------
# Public send function
# -----------------------------------------------------------------------------

def send_rep_emails(results: dict, ff_data: dict) -> None:
    """
    Send one email per rep with Ari, Blair, and Imtiaz CC'd.
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        print("[Email] No SENDGRID_API_KEY — skipping emails.")
        return

    sg         = sendgrid.SendGridAPIClient(api_key=api_key)
    week_label = _week_label()

    print(f"\n[Email] Sending per-rep emails...")

    for oid, data in results.items():
        rep = data["rep"]
        to_email   = resolve_email(rep)
        prefix     = message_prefix()
        dev_note   = f" [routing for {rep['name']}]" if IS_DEV else ""

        print(f"  Sending to {rep['name']} → {to_email}{dev_note}...")

        html_body = _build_html_body(rep, data, week_label, ff_data)

        message = Mail(
            from_email=(EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME),
            subject=f"{prefix}HubSpot Hygiene Audit — {rep['first_name']} — Week of {week_label}{dev_note}",
        )

        message.to = [To(to_email)]
        message.cc = [Cc(addr) for addr in EMAIL_CC]

        message.add_content(Content("text/html", html_body))

        try:
            response = sg.send(message)
            status   = response.status_code
            ok       = 200 <= status < 300
            print(f"  {'Sent' if ok else 'FAILED'} (HTTP {status})")
        except Exception as e:
            print(f"  FAILED — {e}")

        time.sleep(0.5)  # Avoid SendGrid rate limits
