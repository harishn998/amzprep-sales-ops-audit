# =============================================================================
# email_client.py — SendGrid HTML email notifications
# Design: inspired by Pattern email — dark header, clean cards, CTA buttons
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
    REPS, IS_DEV, IS_FRIDAY, resolve_email, message_prefix,
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
    return [addr for addr in cc_list if addr.lower() != to_email.lower()]


# -----------------------------------------------------------------------------
# HTML components
# -----------------------------------------------------------------------------

STYLE = """<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
     font-size:14px;color:#1a1a1a;background:#f0f2f5}
a{color:#1264a3;text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:620px;margin:24px auto}
/* Header — dark navy like Pattern */
.header{background:#0b1829;border-radius:10px 10px 0 0;padding:28px 36px;
        display:flex;align-items:center;justify-content:space-between}
.brand{color:#ffffff;font-size:20px;font-weight:700;letter-spacing:.5px}
.badge{background:rgba(255,255,255,.12);color:#9ab0c8;font-size:11px;
       font-weight:600;padding:4px 12px;border-radius:20px;
       letter-spacing:.8px;text-transform:uppercase}
/* Dev/Fri banner */
.dev-bar{background:#fef3c7;border-left:4px solid #f59e0b;padding:10px 24px;
         font-size:12px;color:#92400e;font-weight:600}
.fri-bar{background:#eff6ff;border-left:4px solid #3b82f6;padding:10px 24px;
         font-size:12px;color:#1e40af;font-weight:600}
/* Hero */
.hero{background:#ffffff;padding:32px 36px 24px}
.hero-name{font-size:26px;font-weight:700;color:#0b1829;line-height:1.2}
.hero-sub{font-size:13px;color:#6b7280;margin-top:6px}
/* KPI cards row */
.kpi-row{background:#ffffff;padding:0 36px 24px;display:flex;gap:12px}
.kpi{flex:1;background:#f8f9fa;border-radius:8px;padding:14px 12px;text-align:center;
     border-top:3px solid #e5e7eb}
.kpi.red{border-top-color:#dc2626}
.kpi.amber{border-top-color:#d97706}
.kpi.green{border-top-color:#16a34a}
.kpi-n{font-size:28px;font-weight:700;line-height:1;color:#0b1829}
.kpi-n.red{color:#dc2626}
.kpi-n.amber{color:#d97706}
.kpi-n.green{color:#16a34a}
.kpi-l{font-size:11px;color:#6b7280;margin-top:4px;font-weight:500}
/* Body */
.body{background:#ffffff;padding:0 36px 8px}
/* Section */
.sec-title{font-size:11px;font-weight:700;color:#6b7280;text-transform:uppercase;
           letter-spacing:.8px;margin:28px 0 12px;padding-bottom:8px;
           border-bottom:1px solid #e5e7eb}
/* Deal card */
.deal-card{border:1px solid #e5e7eb;border-radius:8px;padding:14px 16px;
           margin-bottom:10px;background:#fafafa}
.deal-card:hover{border-color:#d1d5db}
.deal-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.deal-name{font-size:14px;font-weight:600;color:#0b1829;flex:1}
.deal-name a{color:#0b1829}
.deal-name a:hover{color:#1264a3}
.risk-pill{font-size:10px;font-weight:700;padding:3px 10px;border-radius:12px;
           white-space:nowrap;flex-shrink:0;margin-top:2px}
.risk-high{background:#fef2f2;color:#dc2626;border:1px solid #fecaca}
.risk-med{background:#fffbeb;color:#d97706;border:1px solid #fde68a}
.risk-low{background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0}
.deal-meta{font-size:12px;color:#6b7280;margin-top:5px}
.deal-ai{font-size:12px;margin-top:8px;padding-top:8px;border-top:1px dashed #e5e7eb}
.ai-reason{color:#374151}
.ai-action{color:#1264a3;font-weight:500;margin-top:3px}
.hs-btn{display:inline-block;font-size:11px;font-weight:600;color:#1264a3;
        background:#eff6ff;border:1px solid #bfdbfe;border-radius:5px;
        padding:4px 10px;margin-top:8px;text-decoration:none}
/* Summary table */
.sum-table{width:100%;border-collapse:collapse;font-size:13px;margin:4px 0 16px}
.sum-table th{background:#f3f4f6;padding:8px 12px;text-align:left;
              font-size:11px;font-weight:600;color:#6b7280;
              text-transform:uppercase;letter-spacing:.5px}
.sum-table td{padding:8px 12px;border-bottom:1px solid #f3f4f6;color:#374151}
.sum-table td:last-child{font-weight:600;text-align:right;color:#0b1829}
.sum-table tr.total td{background:#f3f4f6;font-weight:700;border-bottom:none}
.sum-table tr.section-gap td{padding:4px;background:transparent;border:none}
/* Contact list */
.contact-row{display:flex;align-items:center;justify-content:space-between;
             padding:9px 0;border-bottom:1px solid #f3f4f6;font-size:13px}
.contact-row:last-child{border-bottom:none}
.contact-name a{color:#0b1829;font-weight:500}
.contact-meta{font-size:11px;color:#6b7280}
.contact-status{font-size:11px;background:#fef3c7;color:#92400e;
                padding:2px 8px;border-radius:10px;font-weight:600}
/* Action items */
.action-box{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;
            padding:16px 20px;margin:24px 0}
.action-box h3{font-size:13px;font-weight:700;color:#0b1829;margin-bottom:10px}
.action-box ol{padding-left:18px;font-size:13px;color:#374151}
.action-box li{margin-bottom:6px;line-height:1.5}
/* Fireflies */
.ff-bar{display:flex;align-items:center;justify-content:space-between;
        background:#f8f9fa;border-radius:8px;padding:12px 16px;
        font-size:13px;margin:16px 0}
.ff-ok{color:#16a34a;font-weight:600}
.ff-warn{color:#d97706;font-weight:600}
/* Footer */
.footer{background:#0b1829;border-radius:0 0 10px 10px;
        padding:20px 36px;text-align:center}
.footer-links{display:flex;justify-content:center;gap:24px;margin-bottom:10px}
.footer-links a{color:#9ab0c8;font-size:12px;text-decoration:none}
.footer-links a:hover{color:#ffffff}
.footer-copy{color:#4b5563;font-size:11px}
.more-note{font-size:12px;color:#6b7280;font-style:italic;margin:4px 0 12px;padding-left:4px}
</style>"""


def _risk_pill(risk: str | None) -> str:
    if not risk:
        return ""
    mapping = {
        "High":   '<span class="risk-pill risk-high">HIGH RISK</span>',
        "Medium": '<span class="risk-pill risk-med">MED RISK</span>',
        "Low":    '<span class="risk-pill risk-low">LOW RISK</span>',
    }
    return mapping.get(risk, "")


def _kpi_class(n: int, warn: int = 5, crit: int = 20) -> str:
    if n == 0:   return "green"
    if n < warn: return "amber"
    return "red"


def _deal_card_html(deal: dict, stat: str) -> str:
    risk     = deal.get("ai_risk")
    reason   = deal.get("ai_reason")
    action   = deal.get("ai_action")
    pill     = _risk_pill(risk)
    ai_block = ""
    if reason:
        ai_block = (
            f'<div class="deal-ai">'
            f'<div class="ai-reason">{reason}</div>'
            + (f'<div class="ai-action">Next step: {action}</div>' if action else "")
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
        f'<a href="{deal["url"]}" class="hs-btn">View in HubSpot</a>'
        f'</div>'
    )


def _build_html_body(rep: dict, data: dict, week_label: str, ff_data: dict) -> str:
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
        f'<div class="dev-bar">DEV TEST — This audit is for: {rep["name"]} | '
        f'Production email: {rep["email"]}</div>'
    ) if IS_DEV else ""

    fri_banner = (
        '<div class="fri-bar">Friday Check-In — unresolved items from Monday\'s audit</div>'
    ) if IS_FRIDAY else ""

    # KPI cards
    kpi_pd_cls = _kpi_class(len(pd))
    kpi_nc_cls = _kpi_class(len(nc))
    kpi_st_cls = _kpi_class(len(st))
    kpi_tot_cls = _kpi_class(grand_total, 10, 50)

    kpi_row = f"""
    <div class="kpi-row">
      <div class="kpi {kpi_pd_cls}"><div class="kpi-n {kpi_pd_cls}">{len(pd)}</div><div class="kpi-l">Past-Due</div></div>
      <div class="kpi {kpi_nc_cls}"><div class="kpi-n {kpi_nc_cls}">{len(nc)}</div><div class="kpi-l">No Contact 14d+</div></div>
      <div class="kpi {kpi_st_cls}"><div class="kpi-n {kpi_st_cls}">{len(st)}</div><div class="kpi-l">Stale Deals</div></div>
      <div class="kpi {kpi_tot_cls}"><div class="kpi-n {kpi_tot_cls}">{grand_total}</div><div class="kpi-l">Total Issues</div></div>
    </div>"""

    # Summary table
    sum_rows = [
        ("Past-due close date (2025+)",   len(pd)),
        ("Stale — no CRM activity 14d+",  len(st)),
        ("No contact logged 14d+",        len(nc)),
        ("Email-sourced, no follow-up",   len(ef)),
        ("Missing deal amount",           len(data["missing_amount"])),
        ("Missing pipeline source",       len(data["missing_source"])),
        ("Missing MRR",                   len(data["missing_mrr"])),
        ("Missing deal status",           len(data["missing_status"])),
    ]
    sum_html = "".join(
        f"<tr><td>{label}</td><td>{n}</td></tr>"
        for label, n in sum_rows
    )
    sum_html += (
        f'<tr><td><strong>Deal issues total</strong></td><td><strong>{deal_total}</strong></td></tr>'
        f'<tr class="section-gap"><td colspan="2"></td></tr>'
        f"<tr><td>Missing lead status</td><td>{len(ml)}</td></tr>"
        f"<tr><td>Stuck in open status (7d+)</td><td>{len(sk)}</td></tr>"
        f"<tr><td>Calls with no notes (30d)</td><td>{len(cn)}</td></tr>"
        f'<tr class="total"><td>TOTAL ISSUES</td><td>{grand_total}</td></tr>'
    )

    summary_section = f"""
    <div class="body">
      <div class="sec-title">Full Issue Breakdown</div>
      <table class="sum-table">
        <thead><tr><th>Issue</th><th style="text-align:right">Count</th></tr></thead>
        <tbody>{sum_html}</tbody>
      </table>
    </div>"""

    # Deal sections
    def _deals_section(title: str, deals: list, stat_fn) -> str:
        if not deals:
            return ""
        shown = deals[:MAX_DEALS_SHOWN]
        cards = "".join(_deal_card_html(d, stat_fn(d)) for d in shown)
        overflow = (
            f'<p class="more-note">+ {len(deals) - MAX_DEALS_SHOWN} more — open HubSpot to view all</p>'
            if len(deals) > MAX_DEALS_SHOWN else ""
        )
        return f"""<div class="body"><div class="sec-title">{title}</div>{cards}{overflow}</div>"""

    past_due_html = _deals_section(
        f"Past-Due Deals — {len(pd)} total, oldest first", pd,
        lambda d: f"Close date: {d['close_date_str'] or 'not set'}"
    )
    no_contact_html = _deals_section(
        f"No Contact Logged in 14+ Days — {len(nc)} total", nc,
        lambda d: "Never contacted" if d["days_since_contact"] is None else f"{d['days_since_contact']} days since last contact"
    )
    stale_html = _deals_section(
        f"Stale Deals — No CRM Activity — {len(st)} total", st,
        lambda d: "No activity ever" if d["days_inactive"] is None else f"{d['days_inactive']} days inactive"
    )
    email_html = _deals_section(
        f"Email-Sourced Deals — No Follow-Up — {len(ef)} total", ef,
        lambda d: "Originated from email thread — no contact logged in HubSpot"
    )

    # Contact sections
    def _contact_row_html(c: dict, meta_html: str = "") -> str:
        return (
            f'<div class="contact-row">'
            f'<div class="contact-name"><a href="{c["url"]}">{c["name"]}</a></div>'
            f'<div class="contact-meta">{meta_html}</div>'
            f'</div>'
        )

    stuck_html = ""
    if sk:
        rows = "".join(
            _contact_row_html(
                c,
                f'<span class="contact-status">{(c.get("lead_status") or "").replace("_"," ").title()}</span>'
                + (f'  {c["days_stuck"]}d stuck' if c.get("days_stuck") else "")
            )
            for c in sk[:MAX_CONTACTS_SHOWN]
        )
        overflow = f'<p class="more-note">+ {len(sk)-MAX_CONTACTS_SHOWN} more</p>' if len(sk) > MAX_CONTACTS_SHOWN else ""
        stuck_html = f'<div class="body"><div class="sec-title">Contacts Stuck in Lead Status — {len(sk)} total, 7d+</div>{rows}{overflow}</div>'

    missing_lead_html = ""
    if ml:
        rows = "".join(_contact_row_html(c) for c in ml[:MAX_CONTACTS_SHOWN])
        overflow = f'<p class="more-note">+ {len(ml)-MAX_CONTACTS_SHOWN} more</p>' if len(ml) > MAX_CONTACTS_SHOWN else ""
        missing_lead_html = f'<div class="body"><div class="sec-title">Contacts Missing Lead Status — {len(ml)} total</div>{rows}{overflow}</div>'

    calls_html = ""
    if cn:
        rows = "".join(f'<div class="contact-row"><div>{c["title"]}</div></div>' for c in cn[:MAX_CONTACTS_SHOWN])
        calls_html = f'<div class="body"><div class="sec-title">Calls Logged With No Notes — {len(cn)} in last 30 days</div>{rows}</div>'

    # Fireflies bar
    if ff["status"] == "OK":
        ff_bar = f'<div class="body"><div class="ff-bar"><span>Fireflies — Previous Week</span><span class="ff-ok">{ff["count"]} transcript(s) recorded</span></div></div>'
    else:
        ff_bar = '<div class="body"><div class="ff-bar"><span>Fireflies — Previous Week</span><span class="ff-warn">No transcripts recorded — check calendar connection</span></div></div>'

    # Action box
    action_box = """<div class="body"><div class="action-box">
      <h3>Action Items This Week</h3>
      <ol>
        <li>Update or close-lost any deals with past-due close dates</li>
        <li>Log a call, email, or note on every deal with no recent contact</li>
        <li>Fill in Deal Amount, Pipeline Source, MRR, and Deal Status on all open deals</li>
        <li>For email-sourced deals — log your first contact engagement in HubSpot</li>
        <li>Advance or close contacts stuck in Attempted to Contact or In Progress</li>
        <li>Add outcome notes to any calls logged without them</li>
        <li>Assign Lead Status to all contacts that are missing one</li>
      </ol>
    </div></div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">{STYLE}</head>
<body>
<div class="wrap">

  <div class="header">
    <div class="brand">AMZ Prep</div>
    <div class="badge">Hygiene Audit</div>
  </div>

  {dev_banner}{fri_banner}

  <div class="hero">
    <div class="hero-name">Hi {rep['first_name']},</div>
    <div class="hero-name">Here is your pipeline health report.</div>
    <div class="hero-sub">Week of {week_label} &nbsp;·&nbsp; {open_count} open deals in review</div>
  </div>

  {kpi_row}
  {summary_section}
  {past_due_html}
  {no_contact_html}
  {stale_html}
  {email_html}
  {stuck_html}
  {missing_lead_html}
  {calls_html}
  {ff_bar}
  {action_box}

  <div class="body" style="padding-bottom:24px">
    <p style="font-size:12px;color:#9ca3af;text-align:center">
      All unresolved issues carry forward every week until resolved.
    </p>
  </div>

  <div class="footer">
    <div class="footer-links">
      <a href="https://amzprep.com">AMZ Prep</a>
      <a href="https://app.hubspot.com/contacts/878268">Open HubSpot</a>
      <a href="mailto:{EMAIL_FROM_ADDRESS}?subject=Unsubscribe">Unsubscribe</a>
    </div>
    <div class="footer-copy">2026 &copy; AMZ Prep &nbsp;·&nbsp; Hygiene Audit &nbsp;·&nbsp; amzprep.com</div>
  </div>

</div>
</body>
</html>"""


# -----------------------------------------------------------------------------
# Send function
# -----------------------------------------------------------------------------

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
        print(f"  [DEV] To: {resolve_email(list(results.values())[0]['rep'])} | CC: (deduped per send)")

    for oid, data in results.items():
        rep      = data["rep"]
        to_email = resolve_email(rep)
        cc_list  = _deduped_cc(to_email, EMAIL_CC)
        subject  = f"{prefix}AMZ Prep {mode_str} — {rep['first_name']} — Week of {week_label}"

        print(f"  {rep['name']} → To: {to_email} | CC: {cc_list}")

        html_body  = _build_html_body(rep, data, week_label, ff_data)
        plain_body = (
            f"{prefix}AMZ Prep {mode_str} — {rep['first_name']}\n"
            f"Week of {week_label} | {data['open_deals']} open deals\n\n"
            f"Past-due:           {len(data['past_due'])}\n"
            f"No recent contact:  {len(data['no_recent_contact'])}\n"
            f"Stale:              {len(data['stale'])}\n"
            f"Stuck lead status:  {len(data.get('stuck_lead_status',[]))}\n"
            f"Missing lead:       {len(data['missing_lead_status'])}\n"
            f"Calls no notes:     {len(data.get('calls_without_notes',[]))}\n\n"
            "View the full HTML version in your email client.\n"
            "Open HubSpot: https://app.hubspot.com/contacts/878268\n\n"
            "— AMZ Prep Hygiene Audit"
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
