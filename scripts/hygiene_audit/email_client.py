# =============================================================================
# email_client.py — SendGrid email notifications
# Spam fixes:
#   - Added text/plain fallback alongside HTML
#   - Added List-Unsubscribe header
#   - Disabled SendGrid click/open tracking (tracking links trigger spam)
#   - Cleaned subject line — removed [routing for X] machine-generated suffix
# =============================================================================

import os
import time
import json
import sendgrid
from sendgrid.helpers.mail import (
    Mail, To, Cc, Content, Header,
    TrackingSettings, ClickTracking, OpenTracking,
)
from datetime import datetime, timedelta, timezone
from config import (
    EMAIL_CC, EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME,
    REPS, IS_DEV, resolve_email, message_prefix,
)

MAX_PAST_DUE_SHOWN = 10
MAX_STALE_SHOWN    = 10
MAX_CONTACTS_SHOWN = 10


def _week_label() -> str:
    today       = datetime.now(tz=timezone.utc)
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    if last_monday.month == last_sunday.month:
        return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%-d, %Y')}"
    return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%B %-d, %Y')}"


def _deduped_cc(to_email: str, cc_list: list) -> list:
    """Remove To: address from CC if it appears there — SendGrid rejects duplicates."""
    to_lower = to_email.lower()
    return [addr for addr in cc_list if addr.lower() != to_lower]


def _build_plain_text(rep: dict, data: dict, week_label: str, ff_data: dict) -> str:
    """Plain text fallback — improves spam score vs HTML-only emails."""
    oid  = rep["owner_id"]
    ff   = ff_data.get(oid, {"count": 0, "status": "NO DATA"})
    prefix = message_prefix()
    dev_note = f"\nDEV TEST — Routing for: {rep['name']} | Prod email: {rep['email']}\n" if IS_DEV else ""

    lines = [
        f"{prefix}HubSpot Hygiene Audit — {rep['first_name']}",
        f"Week of {week_label} | {data['open_deals']} open deals",
        dev_note,
        "SUMMARY",
        "-" * 36,
        f"Past-due close date:       {len(data['past_due'])}",
        f"Stale (14d+ no activity):  {len(data['stale'])}",
        f"Missing deal amount:       {len(data['missing_amount'])}",
        f"Missing pipeline source:   {len(data['missing_source'])}",
        f"Missing MRR:               {len(data['missing_mrr'])}",
        f"Missing deal status:       {len(data['missing_status'])}",
        f"Missing lead status:       {len(data['missing_lead_status'])}",
        "",
    ]

    if data["past_due"]:
        lines.append("TOP PAST-DUE DEALS (oldest first):")
        for i, d in enumerate(data["past_due"][:MAX_PAST_DUE_SHOWN], 1):
            date_str = f"due {d['close_date_str']}" if d["close_date_str"] else "no close date"
            lines.append(f"  {i}. {d['name']} — {date_str}")
            lines.append(f"     {d['url']}")
        lines.append("")

    if data["stale"]:
        lines.append("TOP STALE DEALS (worst first):")
        for i, d in enumerate(data["stale"][:MAX_STALE_SHOWN], 1):
            activity = "no activity ever" if d["days_inactive"] is None else f"{d['days_inactive']}d inactive"
            lines.append(f"  {i}. {d['name']} — {activity}")
            lines.append(f"     {d['url']}")
        lines.append("")

    if data["missing_lead_status"]:
        lines.append("CONTACTS MISSING LEAD STATUS:")
        for i, c in enumerate(data["missing_lead_status"][:MAX_CONTACTS_SHOWN], 1):
            lines.append(f"  {i}. {c['name']}")
            lines.append(f"     {c['url']}")
        lines.append("")

    ff_line = (
        f"Fireflies: {ff['count']} transcript(s) recorded last week — OK"
        if ff["status"] == "OK"
        else "Fireflies: No transcripts found last week — check calendar connection"
    )
    lines.append(ff_line)
    lines.append("")
    lines.append("WHAT TO DO:")
    lines.append("1. Update or close-lost deals with past-due close dates")
    lines.append("2. Log activity or close-lost stale deals")
    lines.append("3. Fill in Deal Amount, Pipeline Source, MRR, Deal Status")
    lines.append("4. Freight-only deals: set MRR to $0")
    lines.append("5. Assign Lead Status to contacts without one")
    lines.append("")
    lines.append("All unresolved issues carry forward weekly until fixed.")
    lines.append("— AMZ Prep Hygiene Audit")

    return "\n".join(lines)


STYLE = """
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
         font-size: 14px; color: #1a1a1a; background: #f5f5f5; margin: 0; padding: 0; }
  .wrapper { max-width: 680px; margin: 24px auto; background: #ffffff;
             border-radius: 8px; overflow: hidden; border: 1px solid #e0e0e0; }
  .dev-banner { background: #fef3c7; border-bottom: 2px solid #f59e0b;
                padding: 10px 24px; font-size: 12px; color: #92400e; font-weight: 600; }
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


def _count_class(n: int) -> str:
    if n == 0:  return "green"
    if n < 10:  return "amber"
    return "red"


def _build_html_body(rep: dict, data: dict, week_label: str, ff_data: dict) -> str:
    oid        = rep["owner_id"]
    ff         = ff_data.get(oid, {"count": 0, "status": "NO DATA"})
    open_count = data["open_deals"]

    dev_banner = ""
    if IS_DEV:
        dev_banner = (
            f'<div class="dev-banner">'
            f'&#9888; DEV TEST &#8212; Audit for: {rep["name"]} | '
            f'Prod email: {rep["email"]}'
            f'</div>'
        )

    def badge(n: int) -> str:
        return f'<span class="count {_count_class(n)}">{n}</span>'

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
        f"<tr><td>{label}</td><td>{badge(n)}</td></tr>"
        for label, n in summary_rows
    )

    past_due_html = ""
    if data["past_due"]:
        items = ""
        for d in data["past_due"][:MAX_PAST_DUE_SHOWN]:
            date_str = f"due {d['close_date_str']}" if d["close_date_str"] else "no close date set"
            items += f'<li><a href="{d["url"]}">{d["name"]}</a> <span class="badge red">{date_str}</span></li>'
        overflow = (
            f'<p class="more">...and {len(data["past_due"]) - MAX_PAST_DUE_SHOWN} more</p>'
            if len(data["past_due"]) > MAX_PAST_DUE_SHOWN else ""
        )
        past_due_html = (
            f'<div class="section-title">Top past-due deals (oldest first)</div>'
            f'<ul class="deal-list">{items}</ul>{overflow}'
        )

    stale_html = ""
    if data["stale"]:
        items = ""
        for d in data["stale"][:MAX_STALE_SHOWN]:
            activity = "no activity ever" if d["days_inactive"] is None else f"{d['days_inactive']}d inactive"
            items += f'<li><a href="{d["url"]}">{d["name"]}</a> <span class="badge amber">{activity}</span></li>'
        overflow = (
            f'<p class="more">...and {len(data["stale"]) - MAX_STALE_SHOWN} more</p>'
            if len(data["stale"]) > MAX_STALE_SHOWN else ""
        )
        stale_html = (
            f'<div class="section-title">Top stale deals (worst first)</div>'
            f'<ul class="deal-list">{items}</ul>{overflow}'
        )

    contacts_html = ""
    if data["missing_lead_status"]:
        items = "".join(
            f'<li><a href="{c["url"]}">{c["name"]}</a></li>'
            for c in data["missing_lead_status"][:MAX_CONTACTS_SHOWN]
        )
        overflow = (
            f'<p class="more">...and {len(data["missing_lead_status"]) - MAX_CONTACTS_SHOWN} more</p>'
            if len(data["missing_lead_status"]) > MAX_CONTACTS_SHOWN else ""
        )
        contacts_html = (
            f'<div class="section-title">Contacts missing lead status</div>'
            f'<ul class="deal-list">{items}</ul>{overflow}'
        )

    if ff["status"] == "OK":
        ff_html = f'<p class="ff-ok">&#10003; {ff["count"]} Fireflies transcript(s) recorded last week</p>'
    else:
        ff_html = '<p class="ff-warn">&#9888; No Fireflies transcripts found. Ensure Fireflies is connected to your calendar.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">{STYLE}</head>
<body>
<div class="wrapper">
  {dev_banner}
  <div class="header">
    <h1>HubSpot Hygiene Audit &#8212; {rep['first_name']}</h1>
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
    AMZ Prep Hygiene Audit &nbsp;|&nbsp; amzprep.com
  </div>
</div>
</body>
</html>"""


def send_rep_emails(results: dict, ff_data: dict) -> None:
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        print("[Email] No SENDGRID_API_KEY — skipping emails.")
        return

    sg         = sendgrid.SendGridAPIClient(api_key=api_key)
    week_label = _week_label()
    prefix     = message_prefix()

    print(f"\n[Email] Sending per-rep emails...")
    if IS_DEV:
        print(f"  [DEV] To: {resolve_email(list(results.values())[0]['rep'])} | CC: (deduped per send)")

    for oid, data in results.items():
        rep      = data["rep"]
        to_email = resolve_email(rep)
        cc_list  = _deduped_cc(to_email, EMAIL_CC)

        # Clean subject — no [routing for X] suffix to avoid spam triggers
        # The DEV banner inside the email body already identifies the rep
        subject = f"{prefix}HubSpot Hygiene Audit — {rep['first_name']} — Week of {week_label}"

        print(f"  {rep['name']} → To: {to_email} | CC: {cc_list}")

        html_body  = _build_html_body(rep, data, week_label, ff_data)
        plain_body = _build_plain_text(rep, data, week_label, ff_data)

        message = Mail()
        message.from_email = (EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME)
        message.subject    = subject
        message.add_to(To(to_email))
        for addr in cc_list:
            message.add_cc(Cc(addr))

        # Add both plain text AND html — improves spam score vs HTML-only
        message.add_content(Content("text/plain", plain_body))
        message.add_content(Content("text/html",  html_body))

        # Disable click and open tracking — tracking pixels/links hurt
        # deliverability for internal transactional emails
        tracking = TrackingSettings()
        tracking.click_tracking  = ClickTracking(enable=False, enable_text=False)
        tracking.open_tracking   = OpenTracking(enable=False)
        message.tracking_settings = tracking

        # List-Unsubscribe header — improves spam classification
        message.add_header(Header(
            "List-Unsubscribe",
            f"<mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe>"
        ))

        try:
            response = sg.send(message)
            status   = response.status_code
            ok       = 200 <= status < 300
            print(f"  {'Sent ✓' if ok else 'FAILED'} (HTTP {status})")
        except Exception as e:
            print(f"  FAILED — {e}")
            if hasattr(e, "body"):
                try:
                    body = json.loads(e.body)
                    for err in body.get("errors", []):
                        print(f"  SendGrid error: [{err.get('field','?')}] {err.get('message','?')}")
                except Exception:
                    print(f"  Raw error body: {e.body}")

        time.sleep(0.5)
