# =============================================================================
# email_client.py — Kiro HTML email (Pattern-matched design)
# =============================================================================
# Design tokens matched to Pattern email:
#   - Dark navy header + OUTLINED badge pill (border only, no fill)
#   - Large bold hero heading (28px)
#   - White body, generous spacing (40px padding)
#   - Prominent centered pill CTA button (blue, border-radius:50px)
#   - KPI metric strip (colour-coded top border)
#   - Deal cards with AI insight block + HubSpot CTA
#   - Minimal footer: copyright + horizontal rule + link row separated by |
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
        return f"{last_monday.strftime('%B %-d')} \u2013 {last_sunday.strftime('%-d, %Y')}"
    return f"{last_monday.strftime('%B %-d')} \u2013 {last_sunday.strftime('%B %-d, %Y')}"


def _deduped_cc(to_email: str, cc_list: list) -> list:
    return [a for a in cc_list if a.lower() != to_email.lower()]


# =============================================================================
# Pattern-matched CSS — table-based layout for Gmail compatibility
# =============================================================================

CSS = """\
<style>
/* Reset */
body,table,td,div,p,a{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}
table,td{mso-table-lspace:0;mso-table-rspace:0}
img{-ms-interpolation-mode:bicubic;border:0;outline:none;text-decoration:none}
*{box-sizing:border-box}

/* Base */
body{margin:0;padding:0;background:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;color:#1f2937;-webkit-font-smoothing:antialiased}
a{color:#2563eb;text-decoration:none}

/* Outer wrapper — white bg, centred */
.email-outer{width:100%;background:#ffffff;padding:0}
.email-wrap{width:100%;max-width:600px;margin:0 auto;background:#ffffff}

/* ── Header (Pattern dark navy) ── */
.hdr{background:#0b1829;padding:24px 40px}
.hdr-inner{display:flex;align-items:center;justify-content:space-between}
.hdr-brand{color:#ffffff;font-size:20px;font-weight:700;letter-spacing:0.2px;margin:0}
/* Outlined badge — border only, no fill (matches Pattern "MIDDLE MILE" pill) */
.hdr-badge{
  border:1.5px solid rgba(255,255,255,0.55);
  color:#ffffff;
  font-size:11px;font-weight:700;
  padding:5px 14px;
  border-radius:50px;
  letter-spacing:1.2px;
  text-transform:uppercase;
  background:transparent;
  white-space:nowrap
}

/* ── Banners ── */
.banner-dev{background:#fef9c3;border-left:4px solid #f59e0b;padding:11px 40px;font-size:12px;color:#92400e;font-weight:600}
.banner-dev a{color:#92400e;text-decoration:underline}
.banner-fri{background:#eff6ff;border-left:4px solid #3b82f6;padding:11px 40px;font-size:12px;color:#1d4ed8;font-weight:600}

/* ── Hero — large heading like Pattern ── */
.hero{padding:40px 40px 28px}
.hero-heading{font-size:28px;font-weight:800;color:#0b1829;line-height:1.25;margin:0 0 10px}
.hero-sub{font-size:14px;color:#6b7280;margin:0;line-height:1.5}
.hero-sub a{color:#6b7280;text-decoration:underline}

/* ── KPI strip ── */
.kpi-outer{padding:0 40px 32px}
.kpi-row{display:flex;gap:12px}
.kpi-cell{flex:1;background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:16px 10px;text-align:center;border-top:3px solid #e5e7eb}
.kpi-cell.c-red   {border-top-color:#ef4444}
.kpi-cell.c-amber {border-top-color:#f59e0b}
.kpi-cell.c-blue  {border-top-color:#3b82f6}
.kpi-cell.c-green {border-top-color:#22c55e}
.kpi-num{font-size:28px;font-weight:800;line-height:1;color:#0b1829;margin:0 0 6px}
.kpi-num.c-red    {color:#dc2626}
.kpi-num.c-amber  {color:#d97706}
.kpi-num.c-blue   {color:#2563eb}
.kpi-num.c-green  {color:#16a34a}
.kpi-lbl{font-size:11px;color:#6b7280;font-weight:500;line-height:1.35;margin:0}

/* ── Section title ── */
.sec-outer{padding:0 40px}
.sec-title{font-size:10px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:1px;margin:28px 0 12px;padding-bottom:10px;border-bottom:1px solid #f3f4f6}

/* ── Issue breakdown table ── */
.issue-tbl{width:100%;border-collapse:collapse;font-size:14px;margin-bottom:8px}
.issue-tbl th{background:#f9fafb;padding:10px 14px;text-align:left;font-size:11px;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e5e7eb}
.issue-tbl th.r{text-align:right}
.issue-tbl td{padding:10px 14px;border-bottom:1px solid #f3f4f6;color:#374151}
.issue-tbl td.r{text-align:right;font-weight:700;color:#111827}
.issue-tbl tr.subtotal td{background:#f9fafb;font-weight:700;border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb}
.issue-tbl tr.subtotal td.r{font-weight:800}
.issue-tbl tr.gap td{padding:6px;border:none;background:#fff}
.issue-tbl tr.grand td{background:#f1f5f9;font-weight:800;font-size:14px;border-top:2px solid #cbd5e1}
.issue-tbl tr.grand td.r{font-size:15px}

/* ── Deal cards ── */
.deal-card{border:1px solid #e5e7eb;border-radius:10px;padding:18px 18px 14px;margin-bottom:12px;background:#fafafa}
.deal-name{font-size:15px;font-weight:700;color:#0b1829;margin:0 0 4px;display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.deal-name a{color:#0b1829}
.deal-name a:hover{color:#1d4ed8}
.deal-stat{font-size:12px;color:#6b7280;margin:0 0 10px}

/* Risk pills */
.pill{display:inline-block;font-size:10px;font-weight:700;padding:3px 11px;border-radius:50px;white-space:nowrap;flex-shrink:0;margin-top:2px}
.pill-high{background:#fef2f2;color:#dc2626;border:1px solid #fecaca}
.pill-med {background:#fffbeb;color:#b45309;border:1px solid #fde68a}
.pill-low {background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0}

/* AI insight block */
.ai-block{background:#f5f3ff;border-left:3px solid #7c3aed;border-radius:0 8px 8px 0;padding:10px 14px;margin:10px 0;font-size:13px;line-height:1.5}
.ai-why{color:#374151;margin:0 0 4px}
.ai-do{color:#1d4ed8;font-weight:600;margin:0;font-style:italic}

/* ── CTA button — Pattern pill style (centered, blue) ── */
.cta-wrap{text-align:center;margin:14px 0 4px}
.cta-btn{
  display:inline-block;
  background:#1d4ed8;
  color:#ffffff;
  font-size:13px;font-weight:700;
  padding:11px 28px;
  border-radius:50px;
  text-decoration:none;
  letter-spacing:0.3px
}
.cta-btn:hover{background:#1e40af}

.more-note{font-size:12px;color:#9ca3af;font-style:italic;margin:4px 0 16px 2px}

/* ── Contact rows ── */
.contact-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid #f3f4f6;font-size:13px}
.contact-row:last-child{border-bottom:none}
.contact-name a{color:#1f2937;font-weight:500}
.tag-status{font-size:11px;background:#fef3c7;color:#92400e;padding:2px 9px;border-radius:50px;font-weight:600;white-space:nowrap}
.tag-days  {font-size:11px;background:#fef2f2;color:#dc2626;padding:2px 9px;border-radius:50px;font-weight:600;margin-left:5px;white-space:nowrap}

/* ── Fireflies bar ── */
.ff-bar{display:flex;align-items:center;justify-content:space-between;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:13px 16px;font-size:13px;margin-bottom:16px}
.ff-ok  {color:#16a34a;font-weight:600}
.ff-warn{color:#d97706;font-weight:600}

/* ── Action box ── */
.action-box{background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:18px 22px;margin-bottom:16px}
.action-title{font-size:13px;font-weight:700;color:#1e40af;margin:0 0 10px}
.action-box ol{margin:0;padding-left:20px;font-size:13px;color:#1e3a5f;line-height:1.9}

/* ── Footer — Pattern style ── */
/* copyright line, then horizontal rule, then link row */
.footer-copy{padding:24px 40px 10px;text-align:left;font-size:12px;color:#6b7280}
.footer-rule{border:none;border-top:1px solid #e5e7eb;margin:0 40px}
.footer-links{padding:12px 40px 28px;font-size:12px;color:#6b7280;text-align:left}
.footer-links a{color:#6b7280;text-decoration:none;margin-right:0}
.footer-links a:hover{color:#374151;text-decoration:underline}
.footer-sep{color:#d1d5db;margin:0 12px}
</style>"""


# =============================================================================
# HTML building blocks
# =============================================================================

def _kpi_color(n: int, warn: int = 1, crit: int = 10) -> str:
    if n == 0:    return "c-green"
    if n < warn:  return "c-amber"
    if n < crit:  return "c-amber"
    return "c-red"


def _risk_pill(risk: str | None) -> str:
    if not risk:
        return ""
    m = {"High": ("HIGH RISK", "pill-high"), "Medium": ("MED RISK", "pill-med"), "Low": ("LOW RISK", "pill-low")}
    label, cls = m.get(risk, (risk, "pill-low"))
    return f'<span class="pill {cls}">{label}</span>'


def _deal_card(deal: dict, stat: str) -> str:
    risk   = deal.get("ai_risk")
    reason = deal.get("ai_reason")
    action = deal.get("ai_action")
    pill   = _risk_pill(risk)

    ai_block = ""
    if reason:
        ai_block = (
            f'<div class="ai-block">'
            f'<p class="ai-why"><strong>Why:</strong> {reason}</p>'
            + (f'<p class="ai-do">&#8594; {action}</p>' if action else "")
            + "</div>"
        )

    return (
        f'<div class="deal-card">'
        f'<div class="deal-name">'
        f'<span><a href="{deal["url"]}">{deal["name"]}</a></span>'
        f'{pill}'
        f'</div>'
        f'<p class="deal-stat">{stat}</p>'
        f'{ai_block}'
        f'<div class="cta-wrap"><a href="{deal["url"]}" class="cta-btn">View in HubSpot &rarr;</a></div>'
        f'</div>'
    )


def _section(title: str, body: str) -> str:
    return (
        f'<div class="sec-outer">'
        f'<div class="sec-title">{title}</div>'
        f'{body}'
        f'</div>'
    )


def _deals_section(title: str, deals: list, stat_fn) -> str:
    if not deals:
        return ""
    cards   = "".join(_deal_card(d, stat_fn(d)) for d in deals[:MAX_DEALS_SHOWN])
    overflow = (
        f'<p class="more-note">+ {len(deals) - MAX_DEALS_SHOWN} more deals — '
        f'<a href="https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals">open HubSpot to view all</a></p>'
        if len(deals) > MAX_DEALS_SHOWN else ""
    )
    return _section(title, cards + overflow)


def _contact_row(c: dict, tags_html: str = "") -> str:
    return (
        f'<div class="contact-row">'
        f'<div class="contact-name"><a href="{c["url"]}">{c["name"]}</a></div>'
        f'<div>{tags_html}</div>'
        f'</div>'
    )


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

    # ── Banners ──────────────────────────────────────────────────────────────
    dev_banner = (
        f'<div class="banner-dev">'
        f'DEV TEST &mdash; This report is for: {rep["name"]} '
        f'(<a href="mailto:{rep["email"]}">{rep["email"]}</a>)'
        f'</div>'
    ) if IS_DEV else ""

    fri_banner = (
        '<div class="banner-fri">Friday Check-In &mdash; summary of open items from Monday\'s audit</div>'
    ) if IS_FRIDAY else ""

    # ── KPI strip ─────────────────────────────────────────────────────────────
    pd_c  = _kpi_color(len(pd))
    nc_c  = _kpi_color(len(nc))
    st_c  = _kpi_color(len(st))
    tot_c = _kpi_color(grand_total, 10, 50)

    kpi_strip = f"""\
<div class="kpi-outer">
  <div class="kpi-row">
    <div class="kpi-cell {pd_c}">
      <p class="kpi-num {pd_c}">{len(pd)}</p>
      <p class="kpi-lbl">Past-Due Deals</p>
    </div>
    <div class="kpi-cell {nc_c}">
      <p class="kpi-num {nc_c}">{len(nc)}</p>
      <p class="kpi-lbl">No Contact 14d+</p>
    </div>
    <div class="kpi-cell {st_c}">
      <p class="kpi-num {st_c}">{len(st)}</p>
      <p class="kpi-lbl">Stale Deals</p>
    </div>
    <div class="kpi-cell {tot_c}">
      <p class="kpi-num {tot_c}">{grand_total}</p>
      <p class="kpi-lbl">Total Issues</p>
    </div>
  </div>
</div>"""

    # ── Issue table ───────────────────────────────────────────────────────────
    def _row(label: str, n: int, cls: str = "") -> str:
        return f'<tr class="{cls}"><td>{label}</td><td class="r">{n}</td></tr>'

    issue_rows = (
        _row("Past-due close date (2025+)",  len(pd))
        + _row("Stale &mdash; no CRM activity 14d+", len(st))
        + _row("No contact logged 14d+",      len(nc))
        + _row("Email-sourced, no follow-up", len(ef))
        + _row("Missing deal amount",         len(data["missing_amount"]))
        + _row("Missing pipeline source",     len(data["missing_source"]))
        + _row("Missing MRR",                 len(data["missing_mrr"]))
        + _row("Missing deal status",         len(data["missing_status"]))
        + _row("Deal issues total",           deal_total, "subtotal")
        + '<tr class="gap"><td colspan="2"></td></tr>'
        + _row("Missing lead status",         len(ml))
        + _row("Stuck in open status (7d+)",  len(sk))
        + _row("Calls with no notes (30d)",   len(cn))
        + _row("TOTAL ISSUES",                grand_total, "grand")
    )

    issue_section = _section(
        "Full Issue Breakdown",
        f'<table class="issue-tbl">'
        f'<thead><tr><th>Issue</th><th class="r">Count</th></tr></thead>'
        f'<tbody>{issue_rows}</tbody>'
        f'</table>'
    )

    # ── Deal sections ─────────────────────────────────────────────────────────
    past_due_html = _deals_section(
        f"Past-Due Deals &mdash; {len(pd)} total, oldest first", pd,
        lambda d: f"Close date: {d['close_date_str'] or 'not set'}"
    )
    nc_html = _deals_section(
        f"No Contact Logged in 14+ Days &mdash; {len(nc)} total", nc,
        lambda d: "Never contacted" if d["days_since_contact"] is None
                  else f"{d['days_since_contact']} days since last contact"
    )
    stale_html = _deals_section(
        f"Stale Deals &mdash; No CRM Activity &mdash; {len(st)} total", st,
        lambda d: "No activity ever" if d["days_inactive"] is None
                  else f"{d['days_inactive']} days inactive"
    )
    email_src_html = _deals_section(
        f"Email-Sourced Deals &mdash; No Follow-Up &mdash; {len(ef)} total", ef,
        lambda d: "Came from email thread &mdash; no contact logged in HubSpot"
    )

    # ── Contact sections ──────────────────────────────────────────────────────
    def _contact_section(title: str, contacts: list, tag_fn) -> str:
        if not contacts:
            return ""
        rows = "".join(_contact_row(c, tag_fn(c)) for c in contacts[:MAX_CONTACTS_SHOWN])
        overflow = (
            f'<p class="more-note">+ {len(contacts) - MAX_CONTACTS_SHOWN} more</p>'
            if len(contacts) > MAX_CONTACTS_SHOWN else ""
        )
        return _section(title, f'<div style="margin-bottom:4px">{rows}</div>{overflow}')

    stuck_html = _contact_section(
        f"Contacts Stuck in Lead Status &mdash; {len(sk)} total, 7d+", sk,
        lambda c: (
            f'<span class="tag-status">{(c.get("lead_status") or "").replace("_"," ").title()}</span>'
            + (f'<span class="tag-days">{c["days_stuck"]}d stuck</span>' if c.get("days_stuck") else "")
        )
    )

    missing_lead_html = _contact_section(
        f"Contacts Missing Lead Status &mdash; {len(ml)} total", ml,
        lambda c: ""
    )

    calls_html = ""
    if cn:
        rows = "".join(
            f'<div class="contact-row"><div class="contact-name">{c["title"]}</div></div>'
            for c in cn[:MAX_CONTACTS_SHOWN]
        )
        calls_html = _section(f"Calls Logged With No Notes &mdash; {len(cn)} in last 30 days", rows)

    # ── Fireflies ─────────────────────────────────────────────────────────────
    if ff["status"] == "OK":
        ff_status = f'<span class="ff-ok">&#10003;&nbsp; {ff["count"]} transcript(s) recorded this week</span>'
    else:
        ff_status = '<span class="ff-warn">&#10007;&nbsp; No transcripts &mdash; check calendar connection</span>'

    ff_section = _section(
        "Fireflies Call Tracking",
        f'<div class="ff-bar"><span style="color:#374151;font-size:13px">Previous week</span>{ff_status}</div>'
    )

    # ── Action box ────────────────────────────────────────────────────────────
    action_box = (
        '<div class="sec-outer">'
        '<div class="action-box">'
        '<p class="action-title">Action Items This Week</p>'
        '<ol>'
        '<li>Update or close-lost any deals with past-due close dates</li>'
        '<li>Log a call, email, or note on every deal with no recent contact</li>'
        '<li>Fill in Deal Amount, Pipeline Source, MRR, and Deal Status</li>'
        '<li>For email-sourced deals &mdash; log your first contact in HubSpot</li>'
        '<li>Advance or close contacts stuck in Attempted to Contact / In Progress</li>'
        '<li>Add outcome notes to any calls logged without them</li>'
        '<li>Assign Lead Status to all contacts that are missing one</li>'
        '</ol>'
        '</div>'
        '</div>'
    )

    # ── Footer — exact Pattern layout ─────────────────────────────────────────
    hs_url = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}"
    footer = (
        f'<p class="footer-copy">2026 &copy; &mdash; Kiro, AMZ Prep</p>'
        f'<hr class="footer-rule">'
        f'<p class="footer-links">'
        f'<a href="https://amzprep.com">AMZ Prep</a>'
        f'<span class="footer-sep">|</span>'
        f'<a href="{hs_url}">HubSpot CRM</a>'
        f'<span class="footer-sep">|</span>'
        f'<a href="{hs_url}/deals">Open Deals</a>'
        f'<span class="footer-sep">|</span>'
        f'<a href="mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe">Unsubscribe</a>'
        f'</p>'
    )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>Kiro Hygiene Report &mdash; {rep['first_name']}</title>
{CSS}
</head>
<body>
<div class="email-outer">
<div class="email-wrap">

  <!-- HEADER -->
  <div class="hdr">
    <div class="hdr-inner">
      <p class="hdr-brand">Kiro</p>
      <span class="hdr-badge">Hygiene Report</span>
    </div>
  </div>

  {dev_banner}{fri_banner}

  <!-- HERO -->
  <div class="hero">
    <h1 class="hero-heading">Hi {rep['first_name']}, here is your pipeline health report.</h1>
    <p class="hero-sub">
      Week of <a href="{hs_url}">{week_label}</a>
      &nbsp;&middot;&nbsp; {open_count} open deals in review
    </p>
  </div>

  <!-- KPI STRIP -->
  {kpi_strip}

  <!-- ISSUE BREAKDOWN -->
  {issue_section}

  <!-- DEAL SECTIONS -->
  {past_due_html}
  {nc_html}
  {stale_html}
  {email_src_html}

  <!-- CONTACT SECTIONS -->
  {stuck_html}
  {missing_lead_html}
  {calls_html}

  <!-- FIREFLIES -->
  {ff_section}

  <!-- ACTION ITEMS -->
  {action_box}

  <!-- NOTE -->
  <div class="sec-outer" style="padding-bottom:32px;padding-top:12px">
    <p style="font-size:12px;color:#9ca3af;text-align:center">
      All unresolved issues carry forward every week until resolved.
    </p>
  </div>

  <!-- FOOTER — Pattern style -->
  {footer}

</div>
</div>
</body>
</html>"""


# =============================================================================
# Send function (unchanged)
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
        first = next(iter(results.values()))["rep"]
        print(f"  [DEV] To: {resolve_email(first)} | CC: (deduped per send)")

    for oid, data in results.items():
        rep      = data["rep"]
        to_email = resolve_email(rep)
        cc_list  = _deduped_cc(to_email, EMAIL_CC)
        subject  = f"{prefix}Kiro {mode_str} \u2014 {rep['first_name']} \u2014 Week of {week_label}"

        print(f"  {rep['name']} \u2192 To: {to_email} | CC: {cc_list}")

        html_body = _build_html(rep, data, week_label, ff_data)

        plain_body = (
            f"{prefix}Kiro {mode_str} \u2014 {rep['first_name']}\n"
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
            print(f"  {'Sent \u2713' if 200 <= status < 300 else 'FAILED'} (HTTP {status})")
        except Exception as e:
            print(f"  FAILED \u2014 {e}")
            if hasattr(e, "body"):
                try:
                    body = json.loads(e.body)
                    for err in body.get("errors", []):
                        print(f"  SendGrid: [{err.get('field','?')}] {err.get('message','?')}")
                except Exception:
                    print(f"  Raw: {e.body}")

        time.sleep(0.5)
