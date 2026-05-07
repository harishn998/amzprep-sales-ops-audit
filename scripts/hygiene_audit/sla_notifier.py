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
import resend
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




def _lead_breach_email_html(breach: dict) -> str:
    rep     = breach["rep"]
    dev_bar = (
        '<div style="background:#fef9c3;border-left:4px solid #f59e0b;padding:10px 36px;'
        'font-size:12px;color:#92400e;font-weight:600;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif">'
        f'DEV TEST &mdash; Prod rep: {rep["name"]} ({rep["email"]})</div>'
    ) if IS_DEV else ""

    extra_rows = ""
    if breach.get("source_missing"):
        extra_rows += (
            '<tr>'
            '<td style="padding:6px 0;color:#64748b;font-size:13px">Pipeline source</td>'
            '<td style="padding:6px 0;font-weight:600;color:#dc2626;font-size:13px">Not set</td>'
            '</tr>'
        )
    elif breach.get("source_invalid"):
        extra_rows += (
            f'<tr>'
            f'<td style="padding:6px 0;color:#64748b;font-size:13px">Pipeline source</td>'
            f'<td style="padding:6px 0;font-weight:600;color:#dc2626;font-size:13px">\'{breach["pipeline_source"]}\' (invalid)</td>'
            f'</tr>'
        )
    if breach.get("referral_missing"):
        extra_rows += (
            '<tr>'
            '<td style="padding:6px 0;color:#64748b;font-size:13px">Referral partner</td>'
            '<td style="padding:6px 0;font-weight:600;color:#dc2626;font-size:13px">Blank (required)</td>'
            '</tr>'
        )

    hs_url = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}"
    S = 'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif'

    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:#f4f6f8;{S}">'
        f'<div style="max-width:600px;margin:0 auto;background:#f4f6f8">'

        # Header
        f'<div style="background:linear-gradient(135deg,#0b1829 0%,#1a3a5c 100%);border-radius:12px 12px 0 0;padding:32px 36px 28px">'
        f'<p style="color:#ffffff;font-size:24px;font-weight:800;letter-spacing:1px;text-transform:uppercase;margin:0 0 5px">Kiro</p>'
        f'<p style="color:#7fb3d3;font-size:12px;font-weight:500;margin:0 0 12px">Sales Ops Agent &mdash; AMZ Prep</p>'
        f'<p style="color:#fca5a5;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;background:rgba(220,38,38,0.2);display:inline-block;padding:4px 14px;border-radius:50px;margin:0">Lead SLA Breach</p>'
        f'</div>'
        f'{dev_bar}'

        # Body
        f'<div style="background:#ffffff;padding:28px 36px">'

        # Alert
        f'<div style="background:#fef2f2;border:1px solid #fecaca;border-left:4px solid #dc2626;border-radius:8px;padding:16px 18px;margin-bottom:20px">'
        f'<p style="font-size:15px;font-weight:700;color:#dc2626;margin:0 0 6px">No Response Within 30 Minutes</p>'
        f'<p style="font-size:13px;color:#374151;margin:0">A new lead was submitted but no call, email, or note was logged within the 30-minute SLA window.</p>'
        f'</div>'

        # Section title
        f'<p style="font-size:10px;font-weight:700;color:#1d4ed8;text-transform:uppercase;letter-spacing:1.5px;padding-bottom:8px;border-bottom:1.5px solid #e2e8f0;margin:0 0 14px">Lead Details</p>'

        # Details table — fully inline
        f'<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:20px">'
        f'<tr><td style="padding:7px 0;color:#64748b;width:130px">Lead</td>'
        f'<td style="padding:7px 0;font-weight:600"><a href="{breach["contact_url"]}" style="color:#1d4ed8;text-decoration:none">{breach["contact_name"]}</a></td></tr>'
        f'<tr><td style="padding:7px 0;color:#64748b">Email</td>'
        f'<td style="padding:7px 0;color:#374151">{breach["contact_email"] or "not set"}</td></tr>'
        f'<tr><td style="padding:7px 0;color:#64748b">Submitted</td>'
        f'<td style="padding:7px 0;color:#374151">{breach["submitted_str"]}</td></tr>'
        f'<tr><td style="padding:7px 0;color:#64748b">SLA deadline</td>'
        f'<td style="padding:7px 0;color:#374151">{breach["deadline_str"]}</td></tr>'
        f'<tr><td style="padding:7px 0;color:#64748b">Overdue</td>'
        f'<td style="padding:7px 0;font-weight:700;color:#dc2626">{breach["hours_overdue"]}h</td></tr>'
        f'{extra_rows}'
        f'</table>'

        # Actions section title
        f'<p style="font-size:10px;font-weight:700;color:#1d4ed8;text-transform:uppercase;letter-spacing:1.5px;padding-bottom:8px;border-bottom:1.5px solid #e2e8f0;margin:0 0 14px">Required Actions</p>'
        f'<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:14px 18px;margin-bottom:20px">'
        f'<ol style="padding-left:18px;font-size:13px;color:#1e3a5f;line-height:1.9;margin:0">'
        f'<li>Log a call or email in HubSpot for this contact immediately</li>'
        f'<li>Set Pipeline Source to the correct dropdown value</li>'
        f'<li>If source = Referral, add the Referral Partner Name</li>'
        f'</ol></div>'

        # CTA button — fully inline, no class
        f'<div style="text-align:center;margin:20px 0 8px">'
        f'<a href="{breach["contact_url"]}" style="display:inline-block;background:#1d4ed8;color:#ffffff;text-decoration:none;font-size:13px;font-weight:700;padding:11px 28px;border-radius:50px;letter-spacing:0.3px">Open Contact in HubSpot &rarr;</a>'
        f'</div></div>'

        # Footer
        f'<p style="padding:16px 36px 6px;font-size:11px;color:#64748b;background:#ffffff;margin:0">2026 &copy; &mdash; Kiro, AMZ Prep</p>'
        f'<hr style="border:none;border-top:1px solid #e2e8f0;margin:0 36px">'
        f'<p style="padding:8px 36px 24px;font-size:11px;color:#64748b;background:#ffffff;margin:0">'
        f'<a href="https://amzprep.com" style="color:#64748b">AMZ Prep</a>'
        f'<span style="color:#cbd5e1;margin:0 8px">|</span>'
        f'<a href="{hs_url}" style="color:#64748b">HubSpot CRM</a>'
        f'<span style="color:#cbd5e1;margin:0 8px">|</span>'
        f'<a href="mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe" style="color:#64748b">Unsubscribe</a>'
        f'</p>'
        f'</div></body></html>'
    )


def _deal_breach_email_html(rep: dict, breaches: list, total_count: int = 0) -> str:
    dev_bar = (
        '<div style="background:#fef9c3;border-left:4px solid #f59e0b;padding:10px 36px;'
        'font-size:12px;color:#92400e;font-weight:600;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif">'
        f'DEV TEST &mdash; Prod rep: {rep["name"]} ({rep["email"]})</div>'
    ) if IS_DEV else ""

    total  = max(total_count, len(breaches))
    hs_url = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals"
    S = 'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif'

    deal_rows = ""
    for d in breaches[:10]:
        days_str = f"{d['days_stale']}d inactive" if d.get("days_stale") is not None else "no activity ever"
        src_flag = ""
        if d.get("pipeline_source") == "not set":
            src_flag = (
                '<span style="display:inline-block;background:#fffbeb;color:#d97706;'
                'font-size:10px;font-weight:700;padding:2px 8px;border-radius:50px;margin-left:6px">'
                'no pipeline source</span>'
            )
        deal_rows += (
            f'<tr>'
            f'<td style="padding:9px 10px;border-bottom:1px solid #f1f5f9;font-size:13px;font-weight:600;color:#0b1829">'
            f'<a href="{d["url"]}" style="color:#0b1829;text-decoration:none">{d["name"]}</a></td>'
            f'<td style="padding:9px 10px;border-bottom:1px solid #f1f5f9;font-size:13px;white-space:nowrap">'
            f'<span style="background:#fef2f2;color:#dc2626;font-size:11px;font-weight:700;padding:2px 8px;border-radius:50px">{days_str}</span>'
            f'{src_flag}</td>'
            f'</tr>'
        )
    if total > 10:
        deal_rows += (
            f'<tr><td colspan="2" style="padding:9px 10px;font-size:12px;color:#94a3b8;font-style:italic">'
            f'+ {total - 10} more &mdash; <a href="{hs_url}" style="color:#1d4ed8">open HubSpot to see all</a>'
            f'</td></tr>'
        )

    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:#f4f6f8;{S}">'
        f'<div style="max-width:600px;margin:0 auto;background:#f4f6f8">'

        # Header
        f'<div style="background:linear-gradient(135deg,#0b1829 0%,#1a3a5c 100%);border-radius:12px 12px 0 0;padding:32px 36px 28px">'
        f'<p style="color:#ffffff;font-size:24px;font-weight:800;letter-spacing:1px;text-transform:uppercase;margin:0 0 5px">Kiro</p>'
        f'<p style="color:#7fb3d3;font-size:12px;font-weight:500;margin:0 0 12px">Sales Ops Agent &mdash; AMZ Prep</p>'
        f'<p style="color:#fca5a5;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;background:rgba(220,38,38,0.2);display:inline-block;padding:4px 14px;border-radius:50px;margin:0">Deal SLA Breach</p>'
        f'</div>'
        f'{dev_bar}'

        # Body
        f'<div style="background:#ffffff;padding:28px 36px">'

        # Alert
        f'<div style="background:#fef2f2;border:1px solid #fecaca;border-left:4px solid #dc2626;border-radius:8px;padding:16px 18px;margin-bottom:20px">'
        f'<p style="font-size:15px;font-weight:700;color:#dc2626;margin:0 0 6px">Deal SLA Breach &mdash; {total} Deal{"s" if total != 1 else ""} Stale 14+ Days</p>'
        f'<p style="font-size:13px;color:#374151;margin:0">The following open deals have had no CRM activity for 14 or more days and are at risk of going cold.</p>'
        f'</div>'

        # Section title
        f'<p style="font-size:10px;font-weight:700;color:#1d4ed8;text-transform:uppercase;letter-spacing:1.5px;padding-bottom:8px;border-bottom:1.5px solid #e2e8f0;margin:0 0 14px">Stale Deals</p>'

        # Deals table — fully inline
        f'<table style="width:100%;border-collapse:collapse;margin-bottom:20px">'
        f'<thead><tr style="background:#f1f5f9">'
        f'<th style="padding:8px 10px;text-align:left;font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;border-bottom:2px solid #e2e8f0">Deal</th>'
        f'<th style="padding:8px 10px;text-align:left;font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;border-bottom:2px solid #e2e8f0">Activity</th>'
        f'</tr></thead>'
        f'<tbody>{deal_rows}</tbody></table>'

        # Actions
        f'<p style="font-size:10px;font-weight:700;color:#1d4ed8;text-transform:uppercase;letter-spacing:1.5px;padding-bottom:8px;border-bottom:1.5px solid #e2e8f0;margin:0 0 14px">Required Actions This Week</p>'
        f'<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:14px 18px;margin-bottom:20px">'
        f'<ol style="padding-left:18px;font-size:13px;color:#1e3a5f;line-height:1.9;margin:0">'
        f'<li>Log a call, email, or note on each deal listed above</li>'
        f'<li>Fill in Pipeline Source on any deal where it is blank</li>'
        f'<li>Close-lost any deal that is no longer being actively pursued</li>'
        f'</ol></div>'

        # CTA — fully inline
        f'<div style="text-align:center;margin:20px 0 8px">'
        f'<a href="{hs_url}" style="display:inline-block;background:#1d4ed8;color:#ffffff;text-decoration:none;font-size:13px;font-weight:700;padding:11px 28px;border-radius:50px;letter-spacing:0.3px">Open HubSpot Deals &rarr;</a>'
        f'</div></div>'

        # Footer
        f'<p style="padding:16px 36px 6px;font-size:11px;color:#64748b;background:#ffffff;margin:0">2026 &copy; &mdash; Kiro, AMZ Prep</p>'
        f'<hr style="border:none;border-top:1px solid #e2e8f0;margin:0 36px">'
        f'<p style="padding:8px 36px 24px;font-size:11px;color:#64748b;background:#ffffff;margin:0">'
        f'<a href="https://amzprep.com" style="color:#64748b">AMZ Prep</a>'
        f'<span style="color:#cbd5e1;margin:0 8px">|</span>'
        f'<a href="{hs_url}" style="color:#64748b">HubSpot Deals</a>'
        f'<span style="color:#cbd5e1;margin:0 8px">|</span>'
        f'<a href="mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe" style="color:#64748b">Unsubscribe</a>'
        f'</p>'
        f'</div></body></html>'
    )



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

    api_key  = os.environ.get("RESEND_API_KEY", "")
    to_email = resolve_email(rep)
    cc_list  = resolve_sla_breach_email_cc(to_email)
    if api_key:
        resend.api_key = api_key
        try:
            r = resend.Emails.send({
                "from":    f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>",
                "to":      [to_email],
                "cc":      cc_list,
                "subject": f"[Lead SLA Breach] {rep['first_name']} — {breach['contact_name']}",
                "html":    _lead_breach_email_html(breach),
                "text":    f"Lead SLA breach: {breach['contact_name']} {breach['hours_overdue']}h overdue. {breach['contact_url']}",
                "headers": {"List-Unsubscribe": f"<mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe>"},
            })
            print(f"    Email sent via Resend → {to_email}")
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

    api_key  = os.environ.get("RESEND_API_KEY", "")
    to_email = resolve_email(rep)
    cc_list  = resolve_sla_breach_email_cc(to_email)
    if api_key:
        resend.api_key = api_key
        try:
            r = resend.Emails.send({
                "from":    f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>",
                "to":      [to_email],
                "cc":      cc_list,
                "subject": f"[Deal SLA Breach] {rep['first_name']} — {total} deal{'s' if total != 1 else ''} stale 14+ days",
                "html":    _deal_breach_email_html(rep, breaches, total_count=total),
                "text":    f"Deal SLA breach: {total} deal(s) stale 14+ days. Open HubSpot: https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals",
                "headers": {"List-Unsubscribe": f"<mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe>"},
            })
            print(f"    Email sent via Resend → {to_email}")
        except Exception as e:
            print(f"    Email FAILED: {e}")
    time.sleep(0.3)
