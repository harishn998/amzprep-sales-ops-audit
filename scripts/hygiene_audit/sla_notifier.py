# =============================================================================
# sla_notifier.py — Immediate SLA breach notifications (Block Kit format)
# =============================================================================
# Handles both Lead SLA (30-min response) and Deal SLA (14d stale) breaches.
# Uses Slack Block Kit for clean, structured, professional messages.
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
from config import (
    ARI, IS_DEV, EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME,
    resolve_slack_ids_for_sla_breach, resolve_email,
    resolve_sla_breach_email_cc, message_prefix,
    HUBSPOT_PORTAL_ID,
)

SLACK_API = "https://slack.com/api"
BOT_NAME  = "Kiro"


# =============================================================================
# Slack helpers
# =============================================================================

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


def _post_blocks(channel_id: str, blocks: list, fallback_text: str) -> bool:
    resp = requests.post(
        f"{SLACK_API}/chat.postMessage",
        json={
            "channel":  channel_id,
            "username": BOT_NAME,
            "text":     fallback_text,
            "blocks":   blocks,
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
# Block Kit primitives
# =============================================================================

def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}

def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}

def _divider() -> dict:
    return {"type": "divider"}

def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}

def _button_section(text: str, url: str, button_label: str) -> dict:
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text},
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": button_label, "emoji": False},
            "url":  url,
            "style": "primary",
        },
    }


# =============================================================================
# Lead SLA breach — Block Kit message
# =============================================================================

def _lead_breach_blocks(breach: dict) -> list:
    rep    = breach["rep"]
    prefix = "[DEV] " if IS_DEV else ""

    # Severity tag based on hours overdue
    hours = breach["hours_overdue"]
    if hours <= 2:
        sev = "🟡  Overdue by"
    elif hours <= 8:
        sev = "🟠  Overdue by"
    else:
        sev = "🔴  Overdue by"

    extra_issues = []
    if breach.get("source_missing"):
        extra_issues.append("Pipeline source not set")
    elif breach.get("source_invalid"):
        extra_issues.append(f"Invalid pipeline source: `{breach['pipeline_source']}`")
    if breach.get("referral_missing"):
        extra_issues.append("Source = Referral but Referral Partner Name is blank")

    blocks = [
        _header(f"🚨 Lead SLA Breach — {rep['name']}"),
        _section(
            f"{prefix}*Sent by Kiro*  ·  <@{ARI['slack_id']}>"
            + (f"\n_DEV MODE — prod rep: {rep['name']} ({rep['email']})_" if IS_DEV else "")
        ),
        _divider(),

        # Lead details
        _section(
            f"*Lead:*  <{breach['contact_url']}|{breach['contact_name']}>"
            + (f"  ·  `{breach['contact_email']}`" if breach.get('contact_email') else "")
        ),
        _section(
            f"*Submitted:*  {breach['submitted_str']}\n"
            f"*SLA deadline:*  {breach['deadline_str']}\n"
            f"{sev}  *{hours}h*"
        ),
        _divider(),

        # What went wrong
        _section(
            "No call, email, or note was logged in HubSpot within *30 minutes* of lead submission."
        ),
    ]

    if extra_issues:
        blocks.append(_section(
            "*Additional issues on this contact:*\n"
            + "\n".join(f"  • {i}" for i in extra_issues)
        ))

    blocks += [
        _divider(),
        _section(
            "*Required actions:*\n"
            "  1.  Log a call or email for this contact immediately\n"
            "  2.  Set Pipeline Source to the correct dropdown value\n"
            "  3.  If source = Referral, add the Referral Partner Name"
        ),
        _button_section(
            f"<{breach['contact_url']}|Open contact in HubSpot>",
            breach["contact_url"],
            "Open Contact →"
        ),
        _context(f"_Kiro SLA Monitor  ·  {breach['submitted_str']}_"),
    ]
    return blocks


# =============================================================================
# Deal SLA breach — Block Kit message
# =============================================================================

def _severity_icon(days: int | None) -> str:
    if days is None:
        return "⚫"   # never touched
    if days >= 90:
        return "🔴"
    if days >= 30:
        return "🟠"
    if days >= 14:
        return "🟡"
    return "⚪"


def _deal_breach_blocks(rep: dict, breaches: list, total_count: int = 0) -> list:
    shown  = len(breaches)
    total  = max(total_count, shown)
    prefix = "[DEV] " if IS_DEV else ""
    hs_url = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals"

    # KPI summary line
    no_src_count = sum(1 for d in breaches if d.get("pipeline_source") == "not set")
    never_count  = sum(1 for d in breaches if d.get("days_stale") is None or (d.get("days_stale") or 0) > 90)

    blocks = [
        _header(f"⚠️  Deal SLA Breach — {rep['name']}"),
        _section(
            f"{prefix}*Sent by Kiro*  ·  <@{ARI['slack_id']}>"
            + (f"\n_DEV MODE — prod rep: {rep['name']} ({rep['email']})_" if IS_DEV else "")
        ),
        _divider(),

        # Summary stats
        _section(
            f"*{total} open deal{'s' if total != 1 else ''} with no CRM activity for 14+ days.*\n"
            f"Showing top {shown} by days inactive."
            + (f"  ·  _{total - shown} more in HubSpot_" if total > shown else "")
        ),
    ]

    if no_src_count or never_count:
        stat_parts = []
        if never_count:
            stat_parts.append(f"🔴  *{never_count}* deals inactive 90+ days")
        if no_src_count:
            stat_parts.append(f"⚠️  *{no_src_count}* missing Pipeline Source")
        blocks.append(_context("  ·  ".join(stat_parts)))

    blocks.append(_divider())

    # Deal rows — one section block per deal for clean alignment
    deal_lines = []
    for i, d in enumerate(breaches, 1):
        days     = d.get("days_stale")
        icon     = _severity_icon(days)
        days_str = f"{days}d inactive" if days is not None else "never active"
        src_tag  = "  ·  `no pipeline source`" if d.get("pipeline_source") == "not set" else ""
        line = f"{icon}  *{i}.*  <{d['url']}|{d['name']}>  ·  _{days_str}_{src_tag}"
        deal_lines.append(line)

    # Slack has a 3000-char limit per section — split into chunks if needed
    chunk, chunk_size = [], 0
    for line in deal_lines:
        if chunk_size + len(line) > 2500 and chunk:
            blocks.append(_section("\n".join(chunk)))
            chunk, chunk_size = [], 0
        chunk.append(line)
        chunk_size += len(line)
    if chunk:
        blocks.append(_section("\n".join(chunk)))

    blocks += [
        _divider(),
        _section(
            "*Required actions this week:*\n"
            "  1.  Log a call, email, or note on each deal listed above\n"
            "  2.  Fill in Pipeline Source on any deal where it's blank\n"
            "  3.  Close-lost any deal that is no longer being actively pursued"
        ),
        _button_section(
            "_Resolve all outstanding deals in HubSpot_",
            hs_url,
            "Open HubSpot Deals →"
        ),
        _context(
            f"_Kiro Deal SLA Monitor  ·  {total} total stale deals for {rep['name']}_"
        ),
    ]
    return blocks


# =============================================================================
# Email helpers (unchanged from previous version)
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
.meta-row{display:flex;gap:24px;margin:10px 0;font-size:13px}
.meta-label{color:#6b7280;font-weight:500;min-width:110px}
.meta-val{color:#111;font-weight:600}
.deal-list{list-style:none;padding:0;margin:12px 0}
.deal-list li{padding:8px 0;border-bottom:1px solid #f3f4f6;font-size:13px}
.deal-list li:last-child{border-bottom:none}
.deal-list a{color:#0b1829;font-weight:600;text-decoration:none}
.days-badge{display:inline-block;background:#fef2f2;color:#dc2626;font-size:11px;
            font-weight:700;padding:2px 8px;border-radius:10px;margin-left:8px}
.src-badge{display:inline-block;background:#fffbeb;color:#d97706;font-size:11px;
           font-weight:700;padding:2px 8px;border-radius:10px;margin-left:4px}
.action-box{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;
            padding:14px 18px;margin:16px 0}
.action-box h3{font-size:13px;font-weight:700;color:#0b1829;margin-bottom:8px}
.action-box ol{padding-left:18px;font-size:13px;color:#374151;line-height:1.8}
.cta-btn{display:inline-block;background:#1d4ed8;color:#fff;font-size:13px;
         font-weight:700;padding:10px 24px;border-radius:50px;text-decoration:none;
         margin:16px 0}
.footer-copy{padding:16px 32px 8px;font-size:12px;color:#6b7280}
.footer-rule{border:none;border-top:1px solid #e5e7eb;margin:0 32px}
.footer-links{padding:10px 32px 24px;font-size:12px;color:#6b7280}
.footer-sep{color:#d1d5db;margin:0 10px}
</style>"""


def _lead_breach_email_html(breach: dict) -> str:
    rep     = breach["rep"]
    dev_bar = (
        f'<div class="dev-bar">DEV TEST — Prod rep: {rep["name"]} ({rep["email"]})</div>'
    ) if IS_DEV else ""

    extra_items = []
    if breach.get("source_missing"):
        extra_items.append("Pipeline source is not set on this contact")
    elif breach.get("source_invalid"):
        extra_items.append(f"Pipeline source '{breach['pipeline_source']}' is not a valid value")
    if breach.get("referral_missing"):
        extra_items.append("Source = Referral but Referral Partner Name is blank")
    extra_html = (
        '<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:10px 14px;margin:12px 0;font-size:13px;color:#92400e">'
        "<strong>Additional issues:</strong><br>"
        + "<br>".join(f"• {i}" for i in extra_items) + "</div>"
    ) if extra_items else ""

    hs_url = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">{_BREACH_STYLE}</head>
<body><div class="wrap">
  <div class="header"><div class="brand">Kiro</div><div class="badge">LEAD SLA BREACH</div></div>
  {dev_bar}
  <div class="body">
    <div class="alert-box">
      <div class="alert-title">SLA Breach — No Response Within 30 Minutes</div>
      <p style="font-size:13px;color:#374151">A new lead was submitted but no call, email, or note was logged within the 30-minute response window.</p>
    </div>
    <div class="meta-row"><span class="meta-label">Lead</span><span class="meta-val"><a href="{breach['contact_url']}" style="color:#0b1829">{breach['contact_name']}</a></span></div>
    <div class="meta-row"><span class="meta-label">Email</span><span class="meta-val">{breach['contact_email'] or 'not set'}</span></div>
    <div class="meta-row"><span class="meta-label">Submitted</span><span class="meta-val">{breach['submitted_str']}</span></div>
    <div class="meta-row"><span class="meta-label">SLA deadline</span><span class="meta-val">{breach['deadline_str']}</span></div>
    <div class="meta-row"><span class="meta-label">Overdue</span><span class="meta-val" style="color:#dc2626">{breach['hours_overdue']}h</span></div>
    {extra_html}
    <div class="action-box"><h3>Required actions</h3><ol>
      <li>Log a call or email in HubSpot for this contact immediately</li>
      <li>Set Pipeline Source to the correct dropdown value</li>
      <li>If source = Referral, add the Referral Partner Name</li>
    </ol></div>
    <a href="{breach['contact_url']}" class="cta-btn">Open Contact in HubSpot &rarr;</a>
  </div>
  <p class="footer-copy">2026 &copy; &mdash; Kiro, AMZ Prep</p>
  <hr class="footer-rule">
  <p class="footer-links">
    <a href="https://amzprep.com" style="color:#6b7280">AMZ Prep</a><span class="footer-sep">|</span>
    <a href="{hs_url}" style="color:#6b7280">HubSpot CRM</a><span class="footer-sep">|</span>
    <a href="mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe" style="color:#6b7280">Unsubscribe</a>
  </p>
</div></body></html>"""


def _deal_breach_email_html(rep: dict, breaches: list, total_count: int = 0) -> str:
    dev_bar = (
        f'<div class="dev-bar">DEV TEST — Prod rep: {rep["name"]} ({rep["email"]})</div>'
    ) if IS_DEV else ""
    total   = max(total_count, len(breaches))
    hs_url  = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals"

    rows = ""
    for d in breaches[:10]:
        days_str = f"{d['days_stale']}d" if d.get("days_stale") is not None else "never active"
        src_flag = '<span class="src-badge">no pipeline source</span>' if d.get("pipeline_source") == "not set" else ""
        rows += (
            f'<li><a href="{d["url"]}">{d["name"]}</a>'
            f'<span class="days-badge">{days_str} inactive</span>{src_flag}</li>'
        )
    if total > 10:
        rows += f'<li style="color:#6b7280;font-style:italic">...and {total - 10} more in HubSpot</li>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">{_BREACH_STYLE}</head>
<body><div class="wrap">
  <div class="header"><div class="brand">Kiro</div><div class="badge">DEAL SLA BREACH</div></div>
  {dev_bar}
  <div class="body">
    <div class="alert-box">
      <div class="alert-title">Deal SLA Breach &mdash; {total} Deal{'s' if total != 1 else ''} Stale 14+ Days</div>
      <p style="font-size:13px;color:#374151">The following open deals have had no CRM activity for 14 or more days and are at risk of going cold.</p>
    </div>
    <ul class="deal-list">{rows}</ul>
    <div class="action-box"><h3>Required actions this week</h3><ol>
      <li>Log a call, email, or note on each deal listed above</li>
      <li>Fill in Pipeline Source on any deal where it is blank</li>
      <li>Close-lost any deal that is no longer being actively pursued</li>
    </ol></div>
    <a href="{hs_url}" class="cta-btn">Open HubSpot Deals &rarr;</a>
  </div>
  <p class="footer-copy">2026 &copy; &mdash; Kiro, AMZ Prep</p>
  <hr class="footer-rule">
  <p class="footer-links">
    <a href="https://amzprep.com" style="color:#6b7280">AMZ Prep</a><span class="footer-sep">|</span>
    <a href="{hs_url}" style="color:#6b7280">HubSpot Deals</a><span class="footer-sep">|</span>
    <a href="mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe" style="color:#6b7280">Unsubscribe</a>
  </p>
</div></body></html>"""


# =============================================================================
# Public notification functions
# =============================================================================

def notify_lead_sla_breach(breach: dict) -> None:
    """Send Block Kit Slack DM + email for a single lead SLA breach."""
    rep = breach["rep"]
    print(f"  [Notify] Lead SLA breach: {rep['name']} — {breach['contact_name']}")

    slack_ids  = resolve_slack_ids_for_sla_breach(rep)
    channel_id = _open_dm(slack_ids)
    if channel_id:
        blocks   = _lead_breach_blocks(breach)
        fallback = f"Lead SLA Breach — {rep['name']} — {breach['contact_name']} — {breach['hours_overdue']}h overdue"
        _post_blocks(channel_id, blocks, fallback)
        print(f"    Slack DM sent to {slack_ids}")

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
    msg.tracking_settings   = tracking
    msg.add_header(Header("List-Unsubscribe", f"<mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe>"))
    try:
        resp = sg.send(msg)
        print(f"    Email sent → {to_email} (HTTP {resp.status_code})")
    except Exception as e:
        print(f"    Email FAILED: {e}")
    time.sleep(0.3)


def notify_deal_sla_breaches(rep: dict, breaches: list, total_count: int = 0) -> None:
    """Send Block Kit Slack DM + email for a rep's deal SLA breaches."""
    if not breaches:
        return

    total = max(total_count, len(breaches))
    print(f"  [Notify] Deal SLA breach: {rep['name']} — {len(breaches)} shown of {total} total")

    slack_ids  = resolve_slack_ids_for_sla_breach(rep)
    channel_id = _open_dm(slack_ids)
    if channel_id:
        blocks   = _deal_breach_blocks(rep, breaches, total_count=total)
        fallback = f"Deal SLA Breach — {rep['name']} — {total} deal(s) stale 14+ days"
        _post_blocks(channel_id, blocks, fallback)
        print(f"    Slack DM sent to {slack_ids}")

    sg       = sendgrid.SendGridAPIClient(api_key=os.environ.get("SENDGRID_API_KEY", ""))
    to_email = resolve_email(rep)
    cc_list  = resolve_sla_breach_email_cc(to_email)
    msg      = Mail()
    msg.from_email = (EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME)
    msg.subject    = f"[Deal SLA Breach] {rep['first_name']} — {total} deal{'s' if total != 1 else ''} stale 14+ days"
    msg.add_to(To(to_email))
    for addr in cc_list:
        msg.add_cc(Cc(addr))
    msg.add_content(Content("text/plain",
        f"Deal SLA breach: {total} open deal(s) stale 14+ days.\n"
        + "\n".join(f"- {d['name']} ({d.get('days_stale','?')}d)" for d in breaches[:5])
        + f"\nOpen HubSpot: https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals"
    ))
    msg.add_content(Content("text/html", _deal_breach_email_html(rep, breaches, total_count=total)))
    tracking = TrackingSettings()
    tracking.click_tracking = ClickTracking(enable=False, enable_text=False)
    tracking.open_tracking  = OpenTracking(enable=False)
    msg.tracking_settings   = tracking
    msg.add_header(Header("List-Unsubscribe", f"<mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe>"))
    try:
        resp = sg.send(msg)
        print(f"    Email sent → {to_email} (HTTP {resp.status_code})")
    except Exception as e:
        print(f"    Email FAILED: {e}")
    time.sleep(0.3)
