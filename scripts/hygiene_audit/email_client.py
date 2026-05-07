# =============================================================================
# email_client.py — Resend HTML emails (ZENO-inspired premium design)
# =============================================================================
# Sender: reports@amzprep.com (Resend, amzprep.com domain verified)
# Provider: Resend SDK (replaces SendGrid)
# Template: Premium dark header + KPI strip + deal cards + clean scorecard
# =============================================================================

import os
import time
import resend
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
    return (
        f"{last_monday.strftime('%Y-%m-%d')} \u2192 {last_sunday.strftime('%Y-%m-%d')}"
    )


def _week_label_display() -> str:
    today       = datetime.now(tz=timezone.utc)
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    if last_monday.month == last_sunday.month:
        return f"{last_monday.strftime('%B %-d')} \u2013 {last_sunday.strftime('%-d, %Y')}"
    return f"{last_monday.strftime('%B %-d')} \u2013 {last_sunday.strftime('%B %-d, %Y')}"


def _deduped_cc(to_email: str, cc_list: list) -> list:
    return [a for a in cc_list if a.lower() != to_email.lower()]


# =============================================================================
# ZENO-inspired CSS
# =============================================================================

CSS = """\
<style>
*{box-sizing:border-box;margin:0;padding:0}
body,table,td{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}
body{background:#f4f6f8;font-size:14px;color:#1a2332}
a{color:#1d4ed8;text-decoration:none}

.email-wrap{width:100%;max-width:640px;margin:0 auto;background:#f4f6f8}

/* ── HEADER — deep navy gradient like ZENO ── */
.hdr{
  background:linear-gradient(135deg,#0b1829 0%,#1a3a5c 100%);
  border-radius:12px 12px 0 0;
  padding:36px 40px 32px;
}
.hdr-brand{color:#ffffff;font-size:28px;font-weight:800;letter-spacing:1px;
           text-transform:uppercase;margin-bottom:6px}
.hdr-sub{color:#7fb3d3;font-size:13px;font-weight:500;letter-spacing:0.3px;
         margin-bottom:16px}
.hdr-range{color:#e2eaf2;font-size:14px;font-weight:700}

/* ── Banners ── */
.banner-dev{background:#fef9c3;border-left:4px solid #f59e0b;
            padding:10px 40px;font-size:12px;color:#92400e;font-weight:600}
.banner-fri{background:#eff6ff;border-left:4px solid #3b82f6;
            padding:10px 40px;font-size:12px;color:#1d4ed8;font-weight:600}

/* ── Body ── */
.body{background:#ffffff;padding:32px 40px}

/* ── Section titles — ZENO style ── */
.sec-title{
  font-size:11px;font-weight:700;color:#1d4ed8;
  text-transform:uppercase;letter-spacing:1.5px;
  padding-bottom:10px;
  border-bottom:1.5px solid #e2e8f0;
  margin:28px 0 16px;
}

/* ── KPI cards strip ── */
.kpi-row{display:flex;gap:10px;margin-bottom:4px}
.kpi{
  flex:1;background:#f8fafc;
  border:1px solid #e2e8f0;
  border-top:3px solid #e2e8f0;
  border-radius:8px;padding:16px 10px;text-align:center
}
.kpi.red{border-top-color:#ef4444}
.kpi.amber{border-top-color:#f59e0b}
.kpi.green{border-top-color:#22c55e}
.kpi-num{font-size:28px;font-weight:800;line-height:1;
         color:#0b1829;margin-bottom:5px}
.kpi-num.red{color:#dc2626}
.kpi-num.amber{color:#d97706}
.kpi-num.green{color:#16a34a}
.kpi-lbl{font-size:10px;color:#64748b;font-weight:600;
          text-transform:uppercase;letter-spacing:.5px}

/* ── Scorecard table — ZENO style ── */
.score-tbl{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:4px}
.score-tbl thead tr{background:#f1f5f9}
.score-tbl th{
  padding:9px 12px;text-align:left;
  font-size:10px;font-weight:700;color:#64748b;
  text-transform:uppercase;letter-spacing:.8px;
  border-bottom:2px solid #e2e8f0
}
.score-tbl th.r{text-align:right}
.score-tbl td{padding:10px 12px;border-bottom:1px solid #f1f5f9;color:#374151}
.score-tbl td.r{text-align:right;font-weight:700;color:#0b1829}
.score-tbl td.warn{text-align:right;font-weight:700;color:#dc2626}
.score-tbl tr.total td{
  background:#f8fafc;font-weight:700;
  border-top:1.5px solid #e2e8f0;font-size:13px
}
.score-tbl tr.grand td{
  background:#f1f5f9;font-weight:800;font-size:14px;
  border-top:2px solid #cbd5e1;color:#0b1829
}
.score-tbl tr.spacer td{padding:4px;border:none}

/* ── Deal cards ── */
.deal-card{
  border:1px solid #e2e8f0;border-radius:8px;
  padding:14px 16px 11px;margin-bottom:10px;background:#fafbfc
}
.deal-top{display:flex;align-items:flex-start;
          justify-content:space-between;gap:10px;margin-bottom:4px}
.deal-name{font-size:14px;font-weight:700;color:#0b1829}
.deal-name a{color:#0b1829}
.deal-stat{font-size:12px;color:#64748b;margin:0 0 8px}
.risk-pill{display:inline-block;font-size:10px;font-weight:700;
           padding:3px 10px;border-radius:50px;white-space:nowrap;flex-shrink:0}
.pill-high{background:#fef2f2;color:#dc2626;border:1px solid #fecaca}
.pill-med {background:#fffbeb;color:#b45309;border:1px solid #fde68a}
.pill-low {background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0}
.ai-box{
  background:#f5f3ff;border-left:3px solid #7c3aed;
  border-radius:0 6px 6px 0;padding:9px 12px;
  margin:8px 0;font-size:12px;line-height:1.5
}
.ai-why{color:#374151;margin:0 0 3px}
.ai-do{color:#1d4ed8;font-weight:600;margin:0;font-style:italic}

/* ── CTA button ── */
.cta-wrap{text-align:center;margin:12px 0 2px}
.cta-btn{
  display:inline-block;
  background:#0b1829;color:#ffffff;
  font-size:12px;font-weight:700;
  padding:9px 22px;border-radius:50px;text-decoration:none;
  letter-spacing:0.2px
}

.more-note{font-size:12px;color:#94a3b8;font-style:italic;margin:4px 0 14px 2px}

/* ── Contact rows ── */
.contact-item{
  display:flex;align-items:center;justify-content:space-between;
  padding:9px 0;border-bottom:1px solid #f1f5f9;font-size:13px
}
.contact-item:last-child{border-bottom:none}
.contact-item a{color:#1a2332;font-weight:500}
.tag-status{font-size:10px;background:#fef3c7;color:#92400e;
            padding:2px 8px;border-radius:50px;font-weight:700;white-space:nowrap}
.tag-days{font-size:10px;background:#fef2f2;color:#dc2626;
          padding:2px 8px;border-radius:50px;font-weight:700;margin-left:5px}

/* ── Fireflies bar ── */
.ff-bar{
  display:flex;align-items:center;justify-content:space-between;
  background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
  padding:12px 16px;font-size:13px;margin-bottom:16px
}
.ff-ok{color:#16a34a;font-weight:600}
.ff-warn{color:#d97706;font-weight:600}

/* ── Action box ── */
.action-box{
  background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;
  padding:16px 20px;margin-bottom:16px
}
.action-title{font-size:11px;font-weight:700;color:#1d4ed8;
              text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}
.action-box ol{padding-left:18px;font-size:13px;color:#1e3a5f;line-height:1.9}

/* ── Footer — ZENO style ── */
.footer-copy{padding:20px 40px 8px;font-size:12px;color:#64748b}
.footer-rule{border:none;border-top:1px solid #e2e8f0;margin:0 40px}
.footer-links{padding:10px 40px 28px;font-size:12px;color:#64748b}
.footer-links a{color:#64748b}
.footer-sep{color:#cbd5e1;margin:0 10px}
</style>"""


# =============================================================================
# HTML builders
# =============================================================================

def _kpi_color(n: int) -> str:
    if n == 0:   return "green"
    if n < 10:   return "amber"
    return "red"


def _risk_pill(risk: str | None) -> str:
    if not risk: return ""
    m = {"High": ("HIGH RISK","pill-high"), "Medium":("MED RISK","pill-med"), "Low":("LOW RISK","pill-low")}
    label, cls = m.get(risk, (risk, "pill-low"))
    return f'<span class="risk-pill {cls}">{label}</span>'


def _deal_card(deal: dict, stat: str) -> str:
    risk   = deal.get("ai_risk")
    reason = deal.get("ai_reason")
    action = deal.get("ai_action")
    pill   = _risk_pill(risk)
    ai_html = ""
    if reason:
        ai_html = (
            f'<div class="ai-box">'
            f'<p class="ai-why"><strong>Why:</strong> {reason}</p>'
            + (f'<p class="ai-do">&rarr; {action}</p>' if action else "")
            + "</div>"
        )
    return (
        f'<div class="deal-card">'
        f'<div class="deal-top">'
        f'<span class="deal-name"><a href="{deal["url"]}">{deal["name"]}</a></span>'
        f'{pill}</div>'
        f'<p class="deal-stat">{stat}</p>'
        f'{ai_html}'
        f'<div class="cta-wrap"><a href="{deal["url"]}" class="cta-btn">View in HubSpot &rarr;</a></div>'
        f'</div>'
    )


def _deals_section(title: str, deals: list, stat_fn) -> str:
    if not deals: return ""
    cards   = "".join(_deal_card(d, stat_fn(d)) for d in deals[:MAX_DEALS_SHOWN])
    overflow = (
        f'<p class="more-note">+ {len(deals) - MAX_DEALS_SHOWN} more &mdash; '
        f'<a href="https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/deals">open HubSpot to view all</a></p>'
        if len(deals) > MAX_DEALS_SHOWN else ""
    )
    return f'<p class="sec-title">{title}</p>{cards}{overflow}'


def _contact_row(c: dict, tags: str = "") -> str:
    return (
        f'<div class="contact-item">'
        f'<div><a href="{c["url"]}">{c["name"]}</a></div>'
        f'<div>{tags}</div>'
        f'</div>'
    )


def _build_html(rep: dict, data: dict, week_label_display: str, week_range: str, ff_data: dict) -> str:
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

    # Banners
    dev_banner = (
        f'<div class="banner-dev">DEV TEST &mdash; This report is for: {rep["name"]} ({rep["email"]})</div>'
    ) if IS_DEV else ""
    fri_banner = (
        '<div class="banner-fri">Friday Check-In &mdash; summary of open items from Monday\'s audit</div>'
    ) if IS_FRIDAY else ""

    # KPI strip
    pd_c  = _kpi_color(len(pd))
    nc_c  = _kpi_color(len(nc))
    st_c  = _kpi_color(len(st))
    tot_c = _kpi_color(grand_total // 5)

    kpi_strip = f"""\
<div class="kpi-row">
  <div class="kpi {pd_c}">
    <p class="kpi-num {pd_c}">{len(pd)}</p>
    <p class="kpi-lbl">Past-Due Deals</p>
  </div>
  <div class="kpi {nc_c}">
    <p class="kpi-num {nc_c}">{len(nc)}</p>
    <p class="kpi-lbl">No Contact 14d+</p>
  </div>
  <div class="kpi {st_c}">
    <p class="kpi-num {st_c}">{len(st)}</p>
    <p class="kpi-lbl">Stale Deals</p>
  </div>
  <div class="kpi {tot_c}">
    <p class="kpi-num {tot_c}">{grand_total}</p>
    <p class="kpi-lbl">Total Issues</p>
  </div>
</div>"""

    # Pipeline health scorecard table
    def _row(label, n, cls=""):
        td_cls = "warn" if n > 0 and cls == "warn" else "r"
        return f"<tr><td>{label}</td><td class='{td_cls}'>{n}</td></tr>"

    scorecard_rows = (
        _row("Past-due close date (2025+)", len(pd), "warn")
        + _row("Stale &mdash; no CRM activity 14d+", len(st), "warn")
        + _row("No contact logged 14d+", len(nc), "warn")
        + _row("Email-sourced, no follow-up", len(ef))
        + _row("Missing deal amount", len(data["missing_amount"]))
        + _row("Missing pipeline source", len(data["missing_source"]))
        + _row("Missing MRR", len(data["missing_mrr"]))
        + _row("Missing deal status", len(data["missing_status"]))
        + '<tr class="total"><td>Deal issues total</td><td class="r">' + str(deal_total) + '</td></tr>'
        + '<tr class="spacer"><td colspan="2"></td></tr>'
        + _row("Missing lead status", len(ml))
        + _row("Stuck in open status (7d+)", len(sk), "warn")
        + _row("Calls with no notes (30d)", len(cn))
        + '<tr class="grand"><td>TOTAL ISSUES</td><td class="r">' + str(grand_total) + '</td></tr>'
    )

    scorecard = (
        f'<table class="score-tbl">'
        f'<thead><tr><th>Issue</th><th class="r">Count</th></tr></thead>'
        f'<tbody>{scorecard_rows}</tbody>'
        f'</table>'
    )

    # Deal sections
    past_due_html = _deals_section(
        f"Past-Due Deals &mdash; {len(pd)} total, oldest first", pd,
        lambda d: f"Close date: {d['close_date_str'] or 'not set'}"
    )
    nc_html = _deals_section(
        f"No Contact Logged 14+ Days &mdash; {len(nc)} total", nc,
        lambda d: "Never contacted" if d["days_since_contact"] is None else f"{d['days_since_contact']} days since last contact"
    )
    stale_html = _deals_section(
        f"Stale Deals &mdash; {len(st)} total", st,
        lambda d: "No activity ever" if d["days_inactive"] is None else f"{d['days_inactive']} days inactive"
    )
    ef_html = _deals_section(
        f"Email-Sourced Deals &mdash; No Follow-Up &mdash; {len(ef)} total", ef,
        lambda d: "Came from email thread &mdash; no contact logged"
    )

    # Contact sections
    def _contact_section(title, contacts, tag_fn) -> str:
        if not contacts: return ""
        rows = "".join(_contact_row(c, tag_fn(c)) for c in contacts[:MAX_CONTACTS_SHOWN])
        overflow = (
            f'<p class="more-note">+ {len(contacts) - MAX_CONTACTS_SHOWN} more</p>'
            if len(contacts) > MAX_CONTACTS_SHOWN else ""
        )
        return f'<p class="sec-title">{title}</p><div>{rows}</div>{overflow}'

    stuck_html = _contact_section(
        f"Contacts Stuck in Lead Status &mdash; {len(sk)} total, 7d+", sk,
        lambda c: (
            f'<span class="tag-status">{(c.get("lead_status") or "").replace("_"," ").title()}</span>'
            + (f'<span class="tag-days">{c["days_stuck"]}d</span>' if c.get("days_stuck") else "")
        )
    )
    missing_lead_html = _contact_section(
        f"Contacts Missing Lead Status &mdash; {len(ml)} total", ml,
        lambda c: ""
    )

    calls_html = ""
    if cn:
        rows = "".join(
            f'<div class="contact-item"><div>{c["title"]}</div></div>'
            for c in cn[:MAX_CONTACTS_SHOWN]
        )
        calls_html = f'<p class="sec-title">Calls With No Notes &mdash; {len(cn)} in last 30 days</p><div>{rows}</div>'

    # Fireflies
    ff_status = (
        f'<span class="ff-ok">&#10003;&nbsp; {ff["count"]} transcript(s) this week</span>'
        if ff["status"] == "OK"
        else '<span class="ff-warn">&#10007;&nbsp; No transcripts &mdash; check calendar connection</span>'
    )
    ff_section = (
        f'<p class="sec-title">Fireflies Call Tracking</p>'
        f'<div class="ff-bar"><span style="color:#374151;font-size:13px">Previous week</span>{ff_status}</div>'
    )

    # Action box
    action_box = (
        '<div class="action-box">'
        '<p class="action-title">Action Items This Week</p>'
        '<ol>'
        '<li>Update or close-lost any deals with past-due close dates</li>'
        '<li>Log a call, email, or note on every deal with no recent contact</li>'
        '<li>Fill in Deal Amount, Pipeline Source, MRR, and Deal Status</li>'
        '<li>For email-sourced deals &mdash; log your first contact in HubSpot</li>'
        '<li>Advance or close contacts stuck in Attempted to Contact / In Progress</li>'
        '<li>Add outcome notes to any calls logged without them</li>'
        '<li>Assign Lead Status to all contacts missing one</li>'
        '</ol>'
        '</div>'
    )

    hs_url = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}"
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kiro &mdash; {rep['first_name']}</title>
{CSS}
</head>
<body>
<div class="email-wrap">

  <div class="hdr">
    <p class="hdr-brand">Kiro</p>
    <p class="hdr-sub">Sales Ops Agent &mdash; AMZ Prep</p>
    <p class="hdr-range">Weekly Digest &middot; {week_range}</p>
  </div>

  {dev_banner}{fri_banner}

  <div class="body">

    <p class="sec-title">Week at a Glance</p>
    <p style="font-size:13px;color:#64748b;margin-bottom:14px">
      {rep['first_name']}'s pipeline &middot; {open_count} open deals in review &middot; Week of {week_label_display}
    </p>
    {kpi_strip}

    <p class="sec-title">Pipeline Health Breakdown</p>
    {scorecard}

    {past_due_html}
    {nc_html}
    {stale_html}
    {ef_html}
    {stuck_html}
    {missing_lead_html}
    {calls_html}
    {ff_section}
    {action_box}

    <p style="font-size:12px;color:#94a3b8;text-align:center;padding:12px 0 20px">
      All unresolved issues carry forward every week until resolved.
    </p>

  </div>

  <p class="footer-copy">2026 &copy; &mdash; Kiro, AMZ Prep</p>
  <hr class="footer-rule">
  <p class="footer-links">
    <a href="https://amzprep.com">AMZ Prep</a><span class="footer-sep">|</span>
    <a href="{hs_url}">HubSpot CRM</a><span class="footer-sep">|</span>
    <a href="{hs_url}/deals">Open Deals</a><span class="footer-sep">|</span>
    <a href="mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe">Unsubscribe</a>
  </p>

</div>
</body>
</html>"""


# =============================================================================
# Resend send function
# =============================================================================

def _send_via_resend(
    to_email: str,
    cc_list: list,
    subject: str,
    html_body: str,
    plain_body: str,
) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        print("  [Email] No RESEND_API_KEY — skipping.")
        return False

    resend.api_key = api_key
    try:
        params: resend.Emails.SendParams = {
            "from":    f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>",
            "to":      [to_email],
            "cc":      cc_list,
            "subject": subject,
            "html":    html_body,
            "text":    plain_body,
            "headers": {
                "List-Unsubscribe": f"<mailto:{EMAIL_FROM_ADDRESS}?subject=unsubscribe>",
                "X-Mailer": "Kiro-SalesOps/2.0",
            },
        }
        result = resend.Emails.send(params)
        print(f"  Sent via Resend &rarr; id={result.get('id','?')} to={to_email}")
        return True
    except Exception as e:
        print(f"  Resend FAILED: {e}")
        return False


# =============================================================================
# Public: send weekly/Friday rep emails
# =============================================================================

def send_rep_emails(results: dict, ff_data: dict) -> None:
    week_range   = _week_label()
    week_display = _week_label_display()
    prefix       = message_prefix()
    mode_str     = "Friday Check-In" if IS_FRIDAY else "Pipeline Health Report"

    print(f"\n[Email] Sending per-rep {mode_str} emails via Resend...")
    if IS_DEV:
        first = next(iter(results.values()))["rep"]
        print(f"  [DEV] To: {resolve_email(first)} from: {EMAIL_FROM_ADDRESS}")

    for oid, data in results.items():
        rep      = data["rep"]
        to_email = resolve_email(rep)
        cc_list  = _deduped_cc(to_email, EMAIL_CC)
        subject  = f"{prefix}Kiro {mode_str} \u2014 {rep['first_name']} \u2014 Week of {week_display}"

        print(f"  {rep['name']} \u2192 {to_email} | cc: {cc_list}")

        html_body = _build_html(rep, data, week_display, week_range, ff_data)

        plain_body = (
            f"{prefix}Kiro {mode_str} \u2014 {rep['first_name']}\n"
            f"Week of {week_display} | {data['open_deals']} open deals\n\n"
            f"Past-due:        {len(data['past_due'])}\n"
            f"No contact:      {len(data['no_recent_contact'])}\n"
            f"Stale:           {len(data['stale'])}\n"
            f"Missing lead:    {len(data['missing_lead_status'])}\n"
            f"Stuck status:    {len(data.get('stuck_lead_status',[]))}\n"
            f"Calls no notes:  {len(data.get('calls_without_notes',[]))}\n\n"
            f"Open HubSpot: https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}\n\n"
            "— Kiro, AMZ Prep Sales Ops"
        )

        _send_via_resend(to_email, cc_list, subject, html_body, plain_body)
        time.sleep(0.3)
