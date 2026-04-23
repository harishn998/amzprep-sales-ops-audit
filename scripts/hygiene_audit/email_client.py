# =============================================================================
# email_client.py — SendGrid HTML emails (Pattern-inspired design v2)
# =============================================================================
# Clean, professional email template:
#   - Dark navy header with Kiro branding + week badge
#   - 4 KPI metric cards (colour-coded by severity)
#   - Full issue breakdown table
#   - Per-deal cards with AI risk pill + reason + action + HubSpot CTA
#   - Contact sections with status badges
#   - Dark footer with links
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
    IS_DEV, IS_FRIDAY, resolve_email, message_prefix,
    HUBSPOT_PORTAL_ID,
)

MAX_DEALS_SHOWN    = 8
MAX_CONTACTS_SHOWN = 8


def _week_label() -> str:
    today       = datetime.now(tz=timezone.utc)
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    if last_monday.month == last_sunday.month:
        return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%-d, %Y')}"
    return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%B %-d, %Y')}"


def _deduped_cc(to_email: str, cc_list: list) -> list:
    return [a for a in cc_list if a.lower() != to_email.lower()]


# =============================================================================
# CSS — inline-friendly, Gmail-compatible
# =============================================================================

CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body,table,td{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}
body{font-size:14px;color:#1f2937;background:#f3f4f6}
a{color:#1e40af;text-decoration:none}
img{border:0;display:block}

/* Layout */
.email-wrap{width:100%;max-width:640px;margin:0 auto}
.email-body{background:#ffffff;border-radius:0 0 12px 12px}

/* Header */
.hdr{background:#0b1829;border-radius:12px 12px 0 0;padding:24px 32px}
.hdr-inner{display:flex;align-items:center;justify-content:space-between}
.hdr-brand{color:#ffffff;font-size:20px;font-weight:700;letter-spacing:0.3px}
.hdr-badge{background:rgba(255,255,255,0.12);color:#93c5fd;font-size:11px;font-weight:700;padding:4px 14px;border-radius:20px;letter-spacing:1px;text-transform:uppercase}

/* Banners */
.banner-dev{background:#fef3c7;border-left:4px solid #f59e0b;padding:10px 32px;font-size:12px;color:#92400e;font-weight:600}
.banner-fri{background:#eff6ff;border-left:4px solid #3b82f6;padding:10px 32px;font-size:12px;color:#1e40af;font-weight:600}

/* Hero */
.hero{padding:28px 32px 20px}
.hero-title{font-size:22px;font-weight:700;color:#0b1829;line-height:1.3;margin-bottom:6px}
.hero-sub{font-size:13px;color:#6b7280}

/* KPI strip */
.kpi-strip{padding:0 32px 24px;display:flex;gap:10px}
.kpi-card{flex:1;background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:14px 12px;text-align:center;border-top:3px solid #e5e7eb}
.kpi-card.red{border-top-color:#ef4444}
.kpi-card.amber{border-top-color:#f59e0b}
.kpi-card.blue{border-top-color:#3b82f6}
.kpi-card.green{border-top-color:#22c55e}
.kpi-num{font-size:26px;font-weight:800;line-height:1;color:#0b1829}
.kpi-num.red{color:#dc2626}
.kpi-num.amber{color:#d97706}
.kpi-num.blue{color:#2563eb}
.kpi-num.green{color:#16a34a}
.kpi-label{font-size:11px;color:#6b7280;margin-top:5px;font-weight:500;line-height:1.3}

/* Sections */
.section{padding:0 32px 4px}
.section-title{font-size:10px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:1px;margin:24px 0 10px;padding-bottom:8px;border-bottom:1px solid #f3f4f6}

/* Issue table */
.issue-table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:4px}
.issue-table th{background:#f9fafb;padding:9px 12px;text-align:left;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e5e7eb}
.issue-table td{padding:9px 12px;border-bottom:1px solid #f3f4f6;color:#374151}
.issue-table td.count{text-align:right;font-weight:700;color:#111827}
.issue-table tr.subtotal td{background:#f9fafb;font-weight:700;border-top:1px solid #e5e7eb}
.issue-table tr.grand td{background:#f3f4f6;font-weight:800;font-size:14px;border-top:2px solid #d1d5db}
.issue-table tr.gap td{padding:4px;border:none}

/* Deal cards */
.deal-card{border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin-bottom:10px;background:#fafafa}
.deal-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:5px}
.deal-name{font-size:14px;font-weight:600;color:#0b1829}
.deal-name a{color:#0b1829}
.deal-meta{font-size:12px;color:#6b7280;margin-bottom:6px}
.risk-pill{display:inline-block;font-size:10px;font-weight:700;padding:3px 10px;border-radius:12px;white-space:nowrap;flex-shrink:0}
.risk-high{background:#fef2f2;color:#dc2626;border:1px solid #fecaca}
.risk-med{background:#fffbeb;color:#d97706;border:1px solid #fde68a}
.risk-low{background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0}
.ai-block{background:#f5f3ff;border-left:3px solid #7c3aed;border-radius:0 6px 6px 0;padding:8px 12px;margin-top:8px;font-size:12px}
.ai-label{font-weight:700;color:#6d28d9;margin-right:6px}
.ai-text{color:#374151}
.ai-action{color:#2563eb;font-weight:500;margin-top:3px;font-style:italic}
.hs-btn{display:inline-block;background:#0b1829;color:#ffffff;font-size:11px;font-weight:600;padding:6px 14px;border-radius:6px;text-decoration:none;margin-top:10px}
.more-note{font-size:12px;color:#9ca3af;font-style:italic;margin:4px 0 12px 4px}

/* Contact list */
.contact-item{display:flex;align-items:center;justify-content:space-between;padding:9px 0;border-bottom:1px solid #f3f4f6;font-size:13px}
.contact-item:last-child{border-bottom:none}
.contact-name a{color:#1f2937;font-weight:500}
.status-tag{font-size:11px;background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:10px;font-weight:600;white-space:nowrap}
.days-tag{font-size:11px;background:#fef2f2;color:#dc2626;padding:2px 8px;border-radius:10px;font-weight:600;margin-left:6px}

/* Fireflies bar */
.ff-bar{display:flex;align-items:center;justify-content:space-between;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:12px 16px;font-size:13px;margin-bottom:16px}
.ff-ok{color:#16a34a;font-weight:600}
.ff-warn{color:#d97706;font-weight:600}

/* Action box */
.action-box{background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:16px 20px;margin-bottom:16px}
.action-box-title{font-size:13px;font-weight:700;color:#1e40af;margin-bottom:10px}
.action-box ol{padding-left:18px;font-size:13px;color:#1e3a5f;line-height:1.8}

/* Footer */
.footer{background:#0b1829;border-radius:0 0 12px 12px;padding:20px 32px;text-align:center}
.footer-links{margin-bottom:10px}
.footer-links a{color:#93c5fd;font-size:12px;text-decoration:none;margin:0 12px}
.footer-copy{color:#374151;font-size:11px}
</style>
"""


def _kpi_color(n: int, warn: int = 3, crit: int = 15) -> str:
    if n == 0:      return "green"
    if n < warn:    return "amber"
    if n < crit:    return "amber"
    return "red"


def _risk_pill_html(risk: str | None) -> str:
    if not risk:
        return ""
    mapping = {
        "High":   '<span class="risk-pill risk-high">HIGH RISK</span>',
        "Medium": '<span class="risk-pill risk-med">MED RISK</span>',
        "Low":    '<span class="risk-pill risk-low">LOW RISK</span>',
    }
    return mapping.get(risk, "")


def _deal_card_html(deal: dict, stat: str) -> str:
    risk   = deal.get("ai_risk")
    reason = deal.get("ai_reason")
    action = deal.get("ai_action")
    pill   = _risk_pill_html(risk)

    ai_block = ""
    if reason:
        ai_block = (
            f'<div class="ai-block">'
            f'<span class="ai-label">AI:</span>'
            f'<span class="ai-text">{reason}</span>'
            + (f'<div class="ai-action">→ {action}</div>' if action else "")
            + "</div>"
        )

    return (
        f'<div class="deal-card">'
        f'<div class="deal-top">'
        f'<div class="deal-name"><a href="{deal["url"]}">{deal["name"]}</a></div>'
        f'{pill}'
        f'</div>'
        f'<div class="deal-meta">{stat}</div>'
        f'{ai_block}'
        f'<a href="{deal["url"]}" class="hs-btn">View in HubSpot &rarr;</a>'
        f'</div>'
    )


def _contact_row_html(c: dict, status_html: str = "") -> str:
    return (
        f'<div class="contact-item">'
        f'<div class="contact-name"><a href="{c["url"]}">{c["name"]}</a></div>'
        f'<div>{status_html}</div>'
        f'</div>'
    )


def _section_html(title: str, body: str) -> str:
    return (
        f'<div class="section">'
        f'<div class="section-title">{title}</div>'
        f'{body}'
        f'</div>'
    )


def _deals_section(title: str, deals: list, stat_fn) -> str:
    if not deals:
        return ""
    shown   = deals[:MAX_DEALS_SHOWN]
    cards   = "".join(_deal_card_html(d, stat_fn(d)) for d in shown)
    overflow = (
        f'<p class="more-note">+ {len(deals) - MAX_DEALS_SHOWN} more — open HubSpot to view all</p>'
        if len(deals) > MAX_DEALS_SHOWN else ""
    )
    return _section_html(title, cards + overflow)


def _build_html(rep: dict, data: dict, week_label: str, ff_data: dict) -> str:
    oid        = rep["owner_id"]
    ff         = ff_data.get(oid, {"count": 0, "status": "NO DATA"})
    open_count = data["open_deals"]

    pd = data["past_due"]
    nc = data["no_recent_contact"]
    st = data["stale"]
    ef = data["created_from_email_no_followup"]
    ml = data["missing_lead_status"]
    sk = data.get("stuck_lead_status", [])
    cn = data.get("calls_without_notes", [])

    deal_total  = sum(len(data[k]) for k in [
        "past_due","stale","no_recent_contact","created_from_email_no_followup",
        "missing_amount","missing_source","missing_mrr","missing_status",
    ])
    grand_total = deal_total + len(ml) + len(sk) + len(cn)

    dev_banner = (
        f'<div class="banner-dev">DEV TEST — This report is for: {rep["name"]} ({rep["email"]})</div>'
    ) if IS_DEV else ""

    fri_banner = (
        '<div class="banner-fri">Friday Check-In — summary of open items from Monday\'s audit</div>'
    ) if IS_FRIDAY else ""

    # KPI strip
    pd_c  = _kpi_color(len(pd))
    nc_c  = _kpi_color(len(nc))
    st_c  = _kpi_color(len(st))
    tot_c = _kpi_color(grand_total, 10, 50)

    kpi_strip = f"""
<div class="kpi-strip">
  <div class="kpi-card {pd_c}">
    <div class="kpi-num {pd_c}">{len(pd)}</div>
    <div class="kpi-label">Past-Due Deals</div>
  </div>
  <div class="kpi-card {nc_c}">
    <div class="kpi-num {nc_c}">{len(nc)}</div>
    <div class="kpi-label">No Contact 14d+</div>
  </div>
  <div class="kpi-card {st_c}">
    <div class="kpi-num {st_c}">{len(st)}</div>
    <div class="kpi-label">Stale Deals</div>
  </div>
  <div class="kpi-card {tot_c}">
    <div class="kpi-num {tot_c}">{grand_total}</div>
    <div class="kpi-label">Total Issues</div>
  </div>
</div>"""

    # Issue table
    issue_rows = [
        ("Past-due close date (2025+)",   len(pd)),
        ("Stale — no CRM activity 14d+",  len(st)),
        ("No contact logged 14d+",        len(nc)),
        ("Email-sourced, no follow-up",   len(ef)),
        ("Missing deal amount",           len(data["missing_amount"])),
        ("Missing pipeline source",       len(data["missing_source"])),
        ("Missing MRR",                   len(data["missing_mrr"])),
        ("Missing deal status",           len(data["missing_status"])),
    ]
    issue_html = "".join(
        f"<tr><td>{label}</td><td class='count'>{n}</td></tr>"
        for label, n in issue_rows
    )
    issue_html += (
        f'<tr class="subtotal"><td>Deal issues total</td><td class="count">{deal_total}</td></tr>'
        f'<tr class="gap"><td colspan="2"></td></tr>'
        f"<tr><td>Missing lead status</td><td class='count'>{len(ml)}</td></tr>"
        f"<tr><td>Stuck in open status (7d+)</td><td class='count'>{len(sk)}</td></tr>"
        f"<tr><td>Calls with no notes (30d)</td><td class='count'>{len(cn)}</td></tr>"
        f'<tr class="grand"><td>TOTAL ISSUES</td><td class="count">{grand_total}</td></tr>'
    )

    issue_section = _section_html(
        "Full Issue Breakdown",
        f'<table class="issue-table">'
        f'<thead><tr><th>Issue</th><th style="text-align:right">Count</th></tr></thead>'
        f'<tbody>{issue_html}</tbody>'
        f'</table>'
    )

    # Deal sections
    past_due_html = _deals_section(
        f"Past-Due Deals — {len(pd)} total, oldest first", pd,
        lambda d: f"Close date: {d['close_date_str'] or 'not set'}"
    )
    nc_html = _deals_section(
        f"No Contact Logged in 14+ Days — {len(nc)} total", nc,
        lambda d: "Never contacted" if d["days_since_contact"] is None else f"{d['days_since_contact']} days since last contact"
    )
    stale_html = _deals_section(
        f"Stale Deals — No CRM Activity — {len(st)} total", st,
        lambda d: "No activity ever" if d["days_inactive"] is None else f"{d['days_inactive']} days inactive"
    )
    email_html = _deals_section(
        f"Email-Sourced Deals — No Follow-Up — {len(ef)} total", ef,
        lambda d: "Came from email thread — no contact logged in HubSpot"
    )

    # Contact sections
    stuck_html = ""
    if sk:
        rows = "".join(
            _contact_row_html(
                c,
                f'<span class="status-tag">{(c.get("lead_status") or "").replace("_"," ").title()}</span>'
                + (f'<span class="days-tag">{c["days_stuck"]}d stuck</span>' if c.get("days_stuck") else "")
            )
            for c in sk[:MAX_CONTACTS_SHOWN]
        )
        overflow = f'<p class="more-note">+ {len(sk) - MAX_CONTACTS_SHOWN} more</p>' if len(sk) > MAX_CONTACTS_SHOWN else ""
        stuck_html = _section_html(f"Contacts Stuck in Lead Status — {len(sk)} total", rows + overflow)

    missing_lead_html = ""
    if ml:
        rows = "".join(_contact_row_html(c) for c in ml[:MAX_CONTACTS_SHOWN])
        overflow = f'<p class="more-note">+ {len(ml) - MAX_CONTACTS_SHOWN} more</p>' if len(ml) > MAX_CONTACTS_SHOWN else ""
        missing_lead_html = _section_html(f"Contacts Missing Lead Status — {len(ml)} total", rows + overflow)

    calls_html = ""
    if cn:
        rows = "".join(
            f'<div class="contact-item"><div class="contact-name">{c["title"]}</div></div>'
            for c in cn[:MAX_CONTACTS_SHOWN]
        )
        calls_html = _section_html(f"Calls Logged With No Notes — {len(cn)} in last 30 days", rows)

    # Fireflies
    if ff["status"] == "OK":
        ff_content = f'<div class="ff-ok">&#10003;  {ff["count"]} transcript(s) recorded this week</div>'
    else:
        ff_content = '<div class="ff-warn">&#10007;  No transcripts recorded — check calendar connection</div>'
    ff_section = _section_html(
        "Fireflies Call Tracking",
        f'<div class="ff-bar"><span style="color:#374151">Fireflies — Previous Week</span>{ff_content}</div>'
    )

    # Action box
    action_box = """
<div class="section">
<div class="action-box">
  <div class="action-box-title">Action Items This Week</div>
  <ol>
    <li>Update or close-lost any deals with past-due close dates</li>
    <li>Log a call, email, or note on every deal with no recent contact</li>
    <li>Fill in Deal Amount, Pipeline Source, MRR, and Deal Status</li>
    <li>For email-sourced deals — log your first contact in HubSpot</li>
    <li>Advance or close contacts stuck in Attempted to Contact or In Progress</li>
    <li>Add outcome notes to any calls logged without them</li>
    <li>Assign Lead Status to all contacts that are missing one</li>
  </ol>
</div>
</div>"""

    hs_url = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kiro Hygiene Report — {rep['first_name']}</title>
{CSS}
</head>
<body>
<div class="email-wrap">

  <div class="hdr">
    <div class="hdr-inner">
      <div class="hdr-brand">Kiro</div>
      <div class="hdr-badge">Hygiene Report</div>
    </div>
  </div>

  {dev_banner}{fri_banner}

  <div class="email-body">

    <div class="hero">
      <div class="hero-title">Hi {rep['first_name']}, here is your pipeline health report.</div>
      <div class="hero-sub">Week of {week_label} &nbsp;&middot;&nbsp; {open_count} open deals in review</div>
    </div>

    {kpi_strip}
    {issue_section}
    {past_due_html}
    {nc_html}
    {stale_html}
    {email_html}
    {stuck_html}
    {missing_lead_html}
    {calls_html}
    {ff_section}
    {action_box}

    <div class="section" style="padding-bottom:24px">
      <p style="font-size:12px;color:#9ca3af;text-align:center">
        All unresolved issues carry forward every week until resolved.
      </p>
    </div>

  </div>

  <div class="footer">
    <div class="footer-links">
      <a href="https://amzprep.com">AMZ Prep</a>
      <a href="{hs_url}">Open HubSpot</a>
      <a href="{hs_url}/deals">Deals</a>
      <a href="mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe">Unsubscribe</a>
    </div>
    <div class="footer-copy">2026 &copy; AMZ Prep &nbsp;&middot;&nbsp; Kiro Sales Ops &nbsp;&middot;&nbsp; amzprep.com</div>
  </div>

</div>
</body>
</html>"""


# =============================================================================
# Send function
# =============================================================================

def send_rep_emails(results: dict, ff_data: dict) -> None:
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        print("[Email] No SENDGRID_API_KEY — skipping.")
        return

    sg         = sendgrid.SendGridAPIClient(api_key=api_key)
    week_label = _week_label()
    prefix     = message_prefix()
    mode_str   = "Friday Check-In" if IS_FRIDAY else "Pipeline Health Report"

    print(f"\n[Email] Sending per-rep {mode_str} emails...")
    if IS_DEV:
        first_rep = next(iter(results.values()))["rep"]
        print(f"  [DEV] To: {resolve_email(first_rep)} | CC: (deduped per send)")

    for oid, data in results.items():
        rep      = data["rep"]
        to_email = resolve_email(rep)
        cc_list  = _deduped_cc(to_email, EMAIL_CC)
        subject  = f"{prefix}Kiro {mode_str} — {rep['first_name']} — Week of {week_label}"

        print(f"  {rep['name']} → To: {to_email} | CC: {cc_list}")

        html_body = _build_html(rep, data, week_label, ff_data)

        plain_body = (
            f"{prefix}Kiro {mode_str} — {rep['first_name']}\n"
            f"Week of {week_label} | {data['open_deals']} open deals\n\n"
            f"Past-due:          {len(data['past_due'])}\n"
            f"No recent contact: {len(data['no_recent_contact'])}\n"
            f"Stale:             {len(data['stale'])}\n"
            f"Missing lead:      {len(data['missing_lead_status'])}\n"
            f"Stuck status:      {len(data.get('stuck_lead_status',[]))}\n"
            f"Calls no notes:    {len(data.get('calls_without_notes',[]))}\n\n"
            f"Open HubSpot: https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}\n\n"
            "— Kiro, AMZ Prep Sales Ops"
        )

        message = Mail()
        message.from_email = (EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME)
        message.subject    = subject
        message.add_to(To(to_email))
        for addr in cc_list:
            message.add_cc(Cc(addr))
        message.add_content(Content("text/plain", plain_body))
        message.add_content(Content("text/html",  html_body))

        tracking = TrackingSettings()
        tracking.click_tracking = ClickTracking(enable=False, enable_text=False)
        tracking.open_tracking  = OpenTracking(enable=False)
        message.tracking_settings = tracking
        message.add_header(Header(
            "List-Unsubscribe",
            f"<mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe>"
        ))

        try:
            resp   = sg.send(message)
            status = resp.status_code
            print(f"  {'Sent' if 200 <= status < 300 else 'FAILED'} (HTTP {status})")
        except Exception as e:
            print(f"  FAILED — {e}")
            if hasattr(e, "body"):
                try:
                    body = json.loads(e.body)
                    for err in body.get("errors", []):
                        print(f"  SendGrid: [{err.get('field','?')}] {err.get('message','?')}")
                except Exception:
                    print(f"  Raw: {e.body}")

        time.sleep(0.5)
