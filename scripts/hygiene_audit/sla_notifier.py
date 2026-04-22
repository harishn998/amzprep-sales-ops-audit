# =============================================================================
# sla_notifier.py — Immediate SLA breach notifications
# =============================================================================
# Called from sla_audit.py (daily cron) whenever a breach is detected.
# Fires Slack DM to rep+Ari AND email to rep+stakeholders — same day, not Monday.
# All routing respects IS_DEV flag: dev mode → dev team only.
# =============================================================================

import os
import time
import json
import requests
import sendgrid
from sendgrid.helpers.mail import (
    Mail, To, Cc, Content, Header,
    TrackingSettings, ClickTracking, OpenTracking,
)
from datetime import datetime, timezone
from config import (
    ARI, IS_DEV, EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME,
    resolve_slack_ids_for_sla_breach, resolve_email,
    resolve_sla_breach_email_cc, message_prefix,
    HUBSPOT_PORTAL_ID,
)

SLACK_API = "https://slack.com/api"


def _slack_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
        "Content-Type":  "application/json",
    }


def _open_dm(user_ids: list) -> str | None:
    unique_ids = list(dict.fromkeys(user_ids))
    resp = requests.post(
        f"{SLACK_API}/conversations.open",
        json={"users": ",".join(unique_ids)},
        headers=_slack_headers(),
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"  [SLA Notifier] Slack DM failed: {data.get('error')}")
        return None
    return data["channel"]["id"]


BOT_NAME = "Kiro"


def _slack_post(channel_id: str, text: str) -> bool:
    resp = requests.post(
        f"{SLACK_API}/chat.postMessage",
        json={
            "channel":  channel_id,
            "text":     text,
            "mrkdwn":  True,
            "username": BOT_NAME,
        },
        headers=_slack_headers(),
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"  [SLA Notifier] Slack post failed: {data.get('error')}")
        return False
    return True


# =============================================================================
# Lead SLA breach — Slack message
# =============================================================================

def _lead_breach_slack_text(breach: dict) -> str:
    rep    = breach["rep"]
    prefix = message_prefix()
    lines  = [
        f"{prefix}*[LEAD SLA BREACH] — {rep['name']}*",
        f"Sent by Kiro  ·  <@{ARI['slack_id']}>",
        "",
        f"*Lead:*  <{breach['contact_url']}|{breach['contact_name']}>",
        f"*Email:*  {breach['contact_email'] or 'not set'}",
        "",
        f"*Submitted:*   {breach['submitted_str']}",
        f"*SLA deadline:* {breach['deadline_str']}",
        f"*Overdue:*     {breach['hours_overdue']}h",
        "",
        "*Issue:* No call, email, or note was logged in HubSpot within 30 minutes of lead submission.",
    ]

    if breach.get("source_missing"):
        lines.append("*Also:* Pipeline source is not set on this contact.")
    elif breach.get("source_invalid"):
        lines.append(f"*Also:* Pipeline source '{breach['pipeline_source']}' is not a valid dropdown value.")
    if breach.get("referral_missing"):
        lines.append("*Also:* Source = Referral but Referral Partner Name is blank.")

    lines += [
        "",
        "*Required actions:*",
        "  1. Log a call or email in HubSpot for this contact immediately",
        "  2. Update Pipeline Source to the correct dropdown value",
        "  3. Add Referral Partner Name if source is Referral",
        "",
        f"<{breach['contact_url']}|Open contact in HubSpot>",
    ]

    if IS_DEV:
        lines.insert(1, f"_DEV MODE — prod rep: {rep['name']} ({rep['email']})_")

    return "\n".join(lines)


# =============================================================================
# Deal SLA breach — Slack message
# =============================================================================

def _deal_breach_slack_text(rep: dict, breaches: list) -> str:
    prefix = message_prefix()
    lines  = [
        f"{prefix}*[DEAL SLA BREACH] — {rep['name']}*",
        f"Sent by Kiro  ·  <@{ARI['slack_id']}>",
        "",
        f"*{len(breaches)} open deal(s) have had no CRM activity for 14+ days.*",
        "_These deals are stale and may be losing momentum._",
        "",
    ]

    for i, d in enumerate(breaches[:8], 1):
        days_str = f"{d['days_stale']}d inactive" if d["days_stale"] is not None else "no activity ever"
        src_flag = "  ·  _no pipeline source_" if d["pipeline_source"] == "not set" else ""
        lines.append(f"  {i:>2}.  <{d['url']}|{d['name']}>  ·  {days_str}{src_flag}")

    if len(breaches) > 8:
        lines.append(f"  _...and {len(breaches) - 8} more_")

    lines += [
        "",
        "*Required actions:*",
        "  1. Log a call, email, or note on each deal this week",
        "  2. Update Pipeline Source on any deal where it's blank",
        "  3. Close-lost any deal that is no longer active",
        "",
        f"<https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals|Open HubSpot Deals>",
    ]

    if IS_DEV:
        lines.insert(1, f"_DEV MODE — prod rep: {rep['name']} ({rep['email']})_")

    return "\n".join(lines)


# =============================================================================
# Email templates
# =============================================================================

_BREACH_STYLE = """<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
     font-size:14px;color:#1a1a1a;background:#f0f2f5}
.wrap{max-width:580px;margin:24px auto}
.header{background:#7c1d1d;border-radius:10px 10px 0 0;padding:24px 32px;
        display:flex;align-items:center;justify-content:space-between}
.brand{color:#fff;font-size:18px;font-weight:700}
.badge{background:rgba(255,255,255,.15);color:#fca5a5;font-size:11px;
       font-weight:700;padding:4px 12px;border-radius:20px;letter-spacing:.8px}
.dev-bar{background:#fef3c7;border-left:4px solid #f59e0b;padding:10px 20px;
         font-size:12px;color:#92400e;font-weight:600}
.body{background:#fff;padding:24px 32px}
.alert-box{background:#fef2f2;border:1px solid #fecaca;border-left:4px solid #dc2626;
           border-radius:8px;padding:16px 20px;margin:16px 0}
.alert-title{font-size:15px;font-weight:700;color:#dc2626;margin-bottom:8px}
.meta-row{display:flex;gap:24px;margin:12px 0;font-size:13px}
.meta-label{color:#6b7280;font-weight:500;min-width:100px}
.meta-val{color:#111;font-weight:600}
.deal-list{list-style:none;padding:0;margin:12px 0}
.deal-list li{padding:8px 0;border-bottom:1px solid #f3f4f6;font-size:13px}
.deal-list li:last-child{border-bottom:none}
.deal-list a{color:#0b1829;font-weight:600;text-decoration:none}
.days-badge{display:inline-block;background:#fef2f2;color:#dc2626;font-size:11px;
            font-weight:700;padding:2px 8px;border-radius:10px;margin-left:8px}
.warn-badge{background:#fffbeb;color:#d97706}
.also-box{background:#fffbeb;border:1px solid #fde68a;border-radius:6px;
          padding:10px 14px;margin:12px 0;font-size:13px;color:#92400e}
.action-box{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;
            padding:14px 18px;margin:16px 0}
.action-box h3{font-size:13px;font-weight:700;color:#0b1829;margin-bottom:8px}
.action-box ol{padding-left:18px;font-size:13px;color:#374151;line-height:1.8}
.cta-btn{display:inline-block;background:#0b1829;color:#fff;font-size:13px;
         font-weight:600;padding:10px 20px;border-radius:6px;text-decoration:none;
         margin:16px 0}
.footer{background:#0b1829;border-radius:0 0 10px 10px;padding:16px 32px;text-align:center}
.footer a{color:#9ab0c8;font-size:11px;text-decoration:none;margin:0 10px}
.footer-copy{color:#4b5563;font-size:10px;margin-top:6px}
</style>"""


def _lead_breach_email_html(breach: dict) -> str:
    rep      = breach["rep"]
    dev_bar  = (
        f'<div class="dev-bar">DEV TEST — Prod rep: {rep["name"]} ({rep["email"]})</div>'
    ) if IS_DEV else ""

    also_items = []
    if breach.get("source_missing"):
        also_items.append("Pipeline source is not set on this contact")
    elif breach.get("source_invalid"):
        also_items.append(f"Pipeline source '{breach['pipeline_source']}' is not a valid value")
    if breach.get("referral_missing"):
        also_items.append("Source = Referral but Referral Partner Name is blank")
    also_html = (
        f'<div class="also-box"><strong>Additional issues:</strong><br>'
        + "<br>".join(f"• {i}" for i in also_items)
        + "</div>"
    ) if also_items else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8">{_BREACH_STYLE}</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="brand">Kiro — AMZ Prep</div>
    <div class="badge">LEAD SLA BREACH</div>
  </div>
  {dev_bar}
  <div class="body">
    <div class="alert-box">
      <div class="alert-title">SLA Breach — No Response Within 30 Minutes</div>
      <p style="font-size:13px;color:#374151">
        A new lead was submitted but no call, email, or note was logged in HubSpot
        within the 30-minute response window.
      </p>
    </div>
    <div class="meta-row">
      <span class="meta-label">Lead</span>
      <span class="meta-val"><a href="{breach['contact_url']}" style="color:#0b1829">{breach['contact_name']}</a></span>
    </div>
    <div class="meta-row">
      <span class="meta-label">Email</span>
      <span class="meta-val">{breach['contact_email'] or 'not set'}</span>
    </div>
    <div class="meta-row">
      <span class="meta-label">Submitted</span>
      <span class="meta-val">{breach['submitted_str']}</span>
    </div>
    <div class="meta-row">
      <span class="meta-label">SLA deadline</span>
      <span class="meta-val">{breach['deadline_str']}</span>
    </div>
    <div class="meta-row">
      <span class="meta-label">Overdue</span>
      <span class="meta-val" style="color:#dc2626">{breach['hours_overdue']}h</span>
    </div>
    {also_html}
    <div class="action-box">
      <h3>Required actions</h3>
      <ol>
        <li>Log a call or email in HubSpot for this contact immediately</li>
        <li>Set Pipeline Source to the correct dropdown value</li>
        <li>If source = Referral, add the Referral Partner Name</li>
      </ol>
    </div>
    <a href="{breach['contact_url']}" class="cta-btn">Open Contact in HubSpot</a>
  </div>
  <div class="footer">
    <a href="https://amzprep.com">AMZ Prep</a>
    <a href="https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}">HubSpot CRM</a>
    <div class="footer-copy">2026 &copy; AMZ Prep &nbsp;·&nbsp; Kiro Sales Ops Agent</div>
  </div>
</div>
</body>
</html>"""


def _deal_breach_email_html(rep: dict, breaches: list) -> str:
    dev_bar = (
        f'<div class="dev-bar">DEV TEST — Prod rep: {rep["name"]} ({rep["email"]})</div>'
    ) if IS_DEV else ""

    rows = ""
    for d in breaches[:10]:
        days_str  = f"{d['days_stale']}d" if d["days_stale"] is not None else "never"
        src_flag  = "" if d["pipeline_source"] != "not set" else '<span style="color:#d97706;font-size:11px"> · no pipeline source</span>'
        rows += (
            f'<li><a href="{d["url"]}">{d["name"]}</a>'
            f'<span class="days-badge">{days_str} inactive</span>{src_flag}</li>'
        )
    if len(breaches) > 10:
        rows += f'<li style="color:#6b7280;font-style:italic">...and {len(breaches)-10} more</li>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8">{_BREACH_STYLE}</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="brand">Kiro — AMZ Prep</div>
    <div class="badge">DEAL SLA BREACH</div>
  </div>
  {dev_bar}
  <div class="body">
    <div class="alert-box">
      <div class="alert-title">Deal SLA Breach — {len(breaches)} Deal(s) Stale 14+ Days</div>
      <p style="font-size:13px;color:#374151">
        The following open deals have had no CRM activity for 14 or more days.
        These deals are at risk of going cold.
      </p>
    </div>
    <ul class="deal-list">{rows}</ul>
    <div class="action-box">
      <h3>Required actions this week</h3>
      <ol>
        <li>Log a call, email, or note on each deal listed above</li>
        <li>Fill in Pipeline Source on any deal where it is blank</li>
        <li>Close-lost any deal that is no longer being actively pursued</li>
      </ol>
    </div>
    <a href="https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals" class="cta-btn">
      Open HubSpot Deals
    </a>
  </div>
  <div class="footer">
    <a href="https://amzprep.com">AMZ Prep</a>
    <a href="https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}">HubSpot CRM</a>
    <div class="footer-copy">2026 &copy; AMZ Prep &nbsp;·&nbsp; Kiro Sales Ops Agent</div>
  </div>
</div>
</body>
</html>"""


# =============================================================================
# Public notification functions
# =============================================================================

def notify_lead_sla_breach(breach: dict) -> None:
    """Send Slack DM + email for a single lead SLA breach."""
    rep = breach["rep"]
    print(f"  [Notify] Lead SLA breach: {rep['name']} — {breach['contact_name']}")

    # Slack
    slack_ids  = resolve_slack_ids_for_sla_breach(rep)
    channel_id = _open_dm(slack_ids)
    if channel_id:
        _slack_post(channel_id, _lead_breach_slack_text(breach))
        print(f"    Slack DM sent to {slack_ids}")

    # Email
    sg       = sendgrid.SendGridAPIClient(api_key=os.environ.get("SENDGRID_API_KEY", ""))
    to_email = resolve_email(rep)
    cc_list  = resolve_sla_breach_email_cc(to_email)
    msg      = Mail()
    msg.from_email = (EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME)
    msg.subject    = f"[Lead SLA Breach] {rep['first_name']} — {breach['contact_name']}"
    msg.add_to(To(to_email))
    for addr in cc_list:
        msg.add_cc(Cc(addr))
    msg.add_content(Content("text/plain",
        f"Lead SLA breach: {breach['contact_name']} — overdue {breach['hours_overdue']}h.\n"
        f"Submitted: {breach['submitted_str']}\nDeadline: {breach['deadline_str']}\n"
        f"Open: {breach['contact_url']}"
    ))
    msg.add_content(Content("text/html", _lead_breach_email_html(breach)))
    tracking = TrackingSettings()
    tracking.click_tracking = ClickTracking(enable=False, enable_text=False)
    tracking.open_tracking  = OpenTracking(enable=False)
    msg.tracking_settings = tracking
    msg.add_header(Header("List-Unsubscribe", f"<mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe>"))

    try:
        resp = sg.send(msg)
        print(f"    Email sent → {to_email} (HTTP {resp.status_code})")
    except Exception as e:
        print(f"    Email FAILED: {e}")

    time.sleep(0.3)


def notify_deal_sla_breaches(rep: dict, breaches: list) -> None:
    """Send Slack DM + email for a rep's deal SLA breaches."""
    if not breaches:
        return

    print(f"  [Notify] Deal SLA breach: {rep['name']} — {len(breaches)} deal(s)")

    # Slack
    slack_ids  = resolve_slack_ids_for_sla_breach(rep)
    channel_id = _open_dm(slack_ids)
    if channel_id:
        _slack_post(channel_id, _deal_breach_slack_text(rep, breaches))
        print(f"    Slack DM sent to {slack_ids}")

    # Email
    sg       = sendgrid.SendGridAPIClient(api_key=os.environ.get("SENDGRID_API_KEY", ""))
    to_email = resolve_email(rep)
    cc_list  = resolve_sla_breach_email_cc(to_email)
    msg      = Mail()
    msg.from_email = (EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME)
    msg.subject    = f"[Deal SLA Breach] {rep['first_name']} — {len(breaches)} deal(s) stale 14+ days"
    msg.add_to(To(to_email))
    for addr in cc_list:
        msg.add_cc(Cc(addr))
    msg.add_content(Content("text/plain",
        f"Deal SLA breach: {len(breaches)} open deal(s) stale 14+ days.\n"
        + "\n".join(f"- {d['name']} ({d['days_stale']}d)" for d in breaches[:5])
        + f"\nOpen HubSpot: https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals"
    ))
    msg.add_content(Content("text/html", _deal_breach_email_html(rep, breaches)))
    tracking = TrackingSettings()
    tracking.click_tracking = ClickTracking(enable=False, enable_text=False)
    tracking.open_tracking  = OpenTracking(enable=False)
    msg.tracking_settings = tracking
    msg.add_header(Header("List-Unsubscribe", f"<mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe>"))

    try:
        resp = sg.send(msg)
        print(f"    Email sent → {to_email} (HTTP {resp.status_code})")
    except Exception as e:
        print(f"    Email FAILED: {e}")

    time.sleep(0.3)
