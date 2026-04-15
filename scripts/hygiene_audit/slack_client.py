# =============================================================================
# slack_client.py — Slack notifications
# Format: all deal lists use clean aligned table rows with dot separators
# =============================================================================

import os
import time
import requests
from datetime import datetime, timezone, timedelta
from config import (
    ARI, REPS, IS_DEV, IS_FRIDAY,
    resolve_slack_ids_for_rep,
    resolve_slack_ids_for_scorecard,
    message_prefix,
)

SLACK_API          = "https://slack.com/api"
MAX_DEALS_SHOWN    = 8
MAX_CONTACTS_SHOWN = 8


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
        "Content-Type":  "application/json",
    }


def _open_dm(user_ids: list) -> str | None:
    unique_ids = list(dict.fromkeys(user_ids))
    resp = requests.post(
        f"{SLACK_API}/conversations.open",
        json={"users": ",".join(unique_ids)},
        headers=_headers(),
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"  [Slack] conversations.open failed: {data.get('error')} | users={unique_ids}")
        return None
    return data["channel"]["id"]


def _post(channel_id: str, text: str) -> bool:
    resp = requests.post(
        f"{SLACK_API}/chat.postMessage",
        json={"channel": channel_id, "text": text, "mrkdwn": True},
        headers=_headers(),
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"  [Slack] chat.postMessage failed: {data.get('error')}")
        return False
    return True


def _week_label() -> str:
    today       = datetime.now(tz=timezone.utc)
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    if last_monday.month == last_sunday.month:
        return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%-d, %Y')}"
    return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%B %-d, %Y')}"


# -----------------------------------------------------------------------------
# Formatting helpers
# -----------------------------------------------------------------------------

def _risk_tag(deal: dict) -> str:
    """Short risk tag for inline use. Empty string if no AI data."""
    risk = deal.get("ai_risk")
    if not risk:
        return ""
    return {"High": "  [HIGH]", "Medium": "  [MED]", "Low": "  [LOW]"}.get(risk, "")


def _ai_lines(deal: dict) -> list:
    """Returns 1–2 indented AI insight lines, or empty list if no AI data."""
    reason = deal.get("ai_reason")
    action = deal.get("ai_action")
    if not reason:
        return []
    lines = [f"      _Reason:_ {reason}"]
    if action:
        lines.append(f"      _Action:_ {action}")
    return lines


def _deal_row(i: int, deal: dict, meta: str) -> list:
    """
    Formats one deal as a clean aligned row + optional AI insight lines.
    meta = the right-hand info (due date, days inactive, etc.)

    Output (example):
      1. Deal Name                     ·  due Mar 15, 2025  [HIGH]
         Reason: No contact in 60 days.
         Action: Send break-up email.
    """
    name_link = f"<{deal['url']}|{deal['name']}>"
    risk      = _risk_tag(deal)
    row       = f"  {i:>2}. {name_link}  ·  {meta}{risk}"
    return [row] + _ai_lines(deal)


def _contact_row(i: int, c: dict, meta: str = "") -> str:
    link = f"<{c['url']}|{c['name']}>"
    suffix = f"  ·  {meta}" if meta else ""
    return f"  {i:>2}. {link}{suffix}"


def _overflow(total: int, shown: int) -> str:
    if total > shown:
        return f"  _...and {total - shown} more — open HubSpot to see all_"
    return ""


# -----------------------------------------------------------------------------
# Scorecard — Ari's consolidated summary
# -----------------------------------------------------------------------------

def _format_scorecard(scorecard_rows: list, week_label: str) -> str:
    prefix      = message_prefix()
    total_deals = sum(r["open_deals"]          for r in scorecard_rows)
    total_pd    = sum(r["past_due"]            for r in scorecard_rows)
    total_stale = sum(r["stale"]               for r in scorecard_rows)
    total_nc    = sum(r["no_recent_contact"]   for r in scorecard_rows)
    total_amt   = sum(r["missing_amount"]      for r in scorecard_rows)
    total_src   = sum(r["missing_source"]      for r in scorecard_rows)
    total_mrr   = sum(r["missing_mrr"]         for r in scorecard_rows)
    total_sta   = sum(r["missing_status"]      for r in scorecard_rows)
    total_lead  = sum(r["missing_lead_status"] for r in scorecard_rows)
    total_stuck = sum(r["stuck_lead_status"]   for r in scorecard_rows)
    total_cnotes= sum(r["calls_without_notes"] for r in scorecard_rows)
    total_email = sum(r["email_no_followup"]   for r in scorecard_rows)

    lines = [
        f"{prefix}*HubSpot Deal & Lead Hygiene Audit*",
        f"Week of {week_label}  |  {len(scorecard_rows)} Reps  |  {total_deals} Open Deals",
    ]
    if IS_DEV:
        lines.append("_DEV MODE — real data, dev recipients only_")

    # ── Deal issue scorecard ──────────────────────────────────────────────────
    lines += [
        "",
        "*Deal Issues*",
        "```",
        f"{'Rep':<12} {'Open':>5} {'PstDue':>7} {'Stale':>6} {'NoCon':>6} {'No$':>5} {'NoSrc':>6} {'NoMRR':>6} {'NoSta':>6}",
        "─" * 65,
    ]
    for r in scorecard_rows:
        first = r["name"].split()[0]
        lines.append(
            f"{first:<12} {r['open_deals']:>5} {r['past_due']:>7} "
            f"{r['stale']:>6} {r['no_recent_contact']:>6} {r['missing_amount']:>5} "
            f"{r['missing_source']:>6} {r['missing_mrr']:>6} {r['missing_status']:>6}"
        )
    lines += [
        "─" * 65,
        f"{'TOTAL':<12} {total_deals:>5} {total_pd:>7} {total_stale:>6} "
        f"{total_nc:>6} {total_amt:>5} {total_src:>6} {total_mrr:>6} {total_sta:>6}",
        "```",
    ]

    # ── Contact issue scorecard ───────────────────────────────────────────────
    lines += [
        "",
        "*Contact & Activity Issues*",
        "```",
        f"{'Rep':<12} {'NoLead':>7} {'Stuck7d':>8} {'CallsNoNote':>12} {'EmailNoFU':>10}",
        "─" * 52,
    ]
    for r in scorecard_rows:
        first = r["name"].split()[0]
        lines.append(
            f"{first:<12} {r['missing_lead_status']:>7} {r['stuck_lead_status']:>8} "
            f"{r['calls_without_notes']:>12} {r['email_no_followup']:>10}"
        )
    lines += [
        "─" * 52,
        f"{'TOTAL':<12} {total_lead:>7} {total_stuck:>8} {total_cnotes:>12} {total_email:>10}",
        "```",
    ]

    # ── Fireflies ─────────────────────────────────────────────────────────────
    lines += ["", "*Fireflies — Previous Week*", "```"]
    for r in scorecard_rows:
        status = "OK" if r["ff_status"] == "OK" else "NO CALLS RECORDED"
        lines.append(f"  {r['name'].split()[0]:<12}  {r['ff_count']:>2} transcript(s)   {status}")
    lines += ["```", "", "_Per-rep audit DMs sent below._"]
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Per-rep weekly message
# -----------------------------------------------------------------------------

def _format_rep_message(rep: dict, data: dict, week_label: str, ff_data: dict) -> str:
    prefix     = message_prefix()
    oid        = rep["owner_id"]
    ff         = ff_data.get(oid, {"count": 0, "status": "NO DATA"})
    open_count = data["open_deals"]

    deal_issue_total = sum(len(data[k]) for k in [
        "past_due", "stale", "no_recent_contact",
        "created_from_email_no_followup",
        "missing_amount", "missing_source", "missing_mrr", "missing_status",
    ])
    contact_issue_total = sum(len(data[k]) for k in [
        "missing_lead_status", "stuck_lead_status", "calls_without_notes",
    ])
    grand_total = deal_issue_total + contact_issue_total

    lines = [
        f"{prefix}*HubSpot Pipeline Hygiene Audit — {rep['name']}*",
        f"Week of {week_label}  |  Sent by <@{ARI['slack_id']}>",
    ]
    if IS_DEV:
        lines += ["", f"_DEV MODE  |  Prod email: {rep['email']}_"]

    # ── Summary table ─────────────────────────────────────────────────────────
    lines += [
        "",
        f"*Pipeline Summary — {open_count} open deals*",
        "```",
        f"{'Issue':<32} {'Count':>5}",
        "─" * 39,
        f"{'Past-due close date (2025+)':<32} {len(data['past_due']):>5}",
        f"{'Stale — no CRM activity 14d+':<32} {len(data['stale']):>5}",
        f"{'No contact logged 14d+':<32} {len(data['no_recent_contact']):>5}",
        f"{'Email-sourced, no follow-up':<32} {len(data['created_from_email_no_followup']):>5}",
        f"{'Missing deal amount':<32} {len(data['missing_amount']):>5}",
        f"{'Missing pipeline source':<32} {len(data['missing_source']):>5}",
        f"{'Missing MRR':<32} {len(data['missing_mrr']):>5}",
        f"{'Missing deal status':<32} {len(data['missing_status']):>5}",
        "─" * 39,
        f"{'Deal issues total':<32} {deal_issue_total:>5}",
        "",
        f"{'Missing lead status':<32} {len(data['missing_lead_status']):>5}",
        f"{'Stuck lead status (7d+)':<32} {len(data['stuck_lead_status']):>5}",
        f"{'Calls with no notes (30d)':<32} {len(data['calls_without_notes']):>5}",
        "─" * 39,
        f"{'TOTAL ISSUES':<32} {grand_total:>5}",
        "```",
    ]

    # ── Past-due deals ────────────────────────────────────────────────────────
    pd = data["past_due"]
    if pd:
        lines += ["", f"*Past-Due Deals — {len(pd)} total, oldest first*"]
        for i, d in enumerate(pd[:MAX_DEALS_SHOWN], 1):
            date_str = d["close_date_str"] or "no close date"
            lines += _deal_row(i, d, f"due {date_str}")
        lines.append(_overflow(len(pd), MAX_DEALS_SHOWN))

    # ── No recent contact ─────────────────────────────────────────────────────
    nc = data["no_recent_contact"]
    if nc:
        lines += ["", f"*No Contact Logged in 14+ Days — {len(nc)} total*"]
        for i, d in enumerate(nc[:MAX_DEALS_SHOWN], 1):
            meta = (
                "never contacted"
                if d["days_since_contact"] is None
                else f"{d['days_since_contact']}d since last contact"
            )
            lines += _deal_row(i, d, meta)
        lines.append(_overflow(len(nc), MAX_DEALS_SHOWN))

    # ── Stale deals ───────────────────────────────────────────────────────────
    st = data["stale"]
    if st:
        lines += ["", f"*Stale Deals — No CRM Activity — {len(st)} total*"]
        for i, d in enumerate(st[:MAX_DEALS_SHOWN], 1):
            meta = (
                "no activity ever"
                if d["days_inactive"] is None
                else f"{d['days_inactive']}d inactive"
            )
            lines += _deal_row(i, d, meta)
        lines.append(_overflow(len(st), MAX_DEALS_SHOWN))

    # ── Email-sourced, no follow-up ───────────────────────────────────────────
    ef = data["created_from_email_no_followup"]
    if ef:
        lines += ["", f"*Email-Sourced Deals — No Follow-Up Logged — {len(ef)} total*"]
        for i, d in enumerate(ef[:MAX_DEALS_SHOWN], 1):
            lines += _deal_row(i, d, "email source  ·  no contact ever logged")
        lines.append(_overflow(len(ef), MAX_DEALS_SHOWN))

    # ── Stuck lead status (L4) ────────────────────────────────────────────────
    sk = data["stuck_lead_status"]
    if sk:
        lines += ["", f"*Contacts Stuck in Lead Status 7+ Days — {len(sk)} total*"]
        lines.append("_Contacts in 'Attempted to Contact' or 'In Progress' with no update_")
        for i, c in enumerate(sk[:MAX_CONTACTS_SHOWN], 1):
            days_str = f"{c['days_stuck']}d" if c["days_stuck"] is not None else "unknown"
            status   = c.get("lead_status", "unknown").replace("_", " ").title()
            lines.append(_contact_row(i, c, f"{status}  ·  {days_str} stuck"))
        lines.append(_overflow(len(sk), MAX_CONTACTS_SHOWN))

    # ── Missing lead status (L1) ──────────────────────────────────────────────
    ml = data["missing_lead_status"]
    if ml:
        lines += ["", f"*Contacts Missing Lead Status — {len(ml)} total*"]
        for i, c in enumerate(ml[:MAX_CONTACTS_SHOWN], 1):
            lines.append(_contact_row(i, c))
        lines.append(_overflow(len(ml), MAX_CONTACTS_SHOWN))

    # ── Calls without notes (E2) ──────────────────────────────────────────────
    cn = data["calls_without_notes"]
    if cn:
        lines += ["", f"*Calls Logged With No Notes — {len(cn)} in last 30 days*"]
        for i, c in enumerate(cn[:MAX_CONTACTS_SHOWN], 1):
            lines.append(f"  {i:>2}. {c['title']}")
        lines.append(_overflow(len(cn), MAX_CONTACTS_SHOWN))

    # ── Fireflies ─────────────────────────────────────────────────────────────
    ff_str = (
        f"{ff['count']} transcript(s) recorded — OK"
        if ff["status"] == "OK"
        else "No transcripts recorded — check Fireflies is connected to calendar"
    )
    lines += ["", f"*Fireflies — Previous Week*", f"  {ff_str}"]

    # ── Action items ──────────────────────────────────────────────────────────
    lines += [
        "",
        "*Action Items This Week*",
        "  1. Update or close-lost any deals with past-due close dates",
        "  2. Log a call, email, or note on every deal with no recent contact",
        "  3. Fill in Deal Amount, Pipeline Source, MRR, and Deal Status",
        "  4. For email-sourced deals — log first call or email engagement in HubSpot",
        "  5. Advance or close contacts stuck in Attempted to Contact / In Progress",
        "  6. Add outcome notes to any calls logged without them",
        "  7. Assign Lead Status to contacts without one",
        "",
        "_All unresolved issues carry forward every week until fixed._",
    ]

    # Remove any empty overflow lines
    return "\n".join(l for l in lines if l is not None and l != "")


# -----------------------------------------------------------------------------
# Friday reminder — shorter mid-week check-in
# -----------------------------------------------------------------------------

def _format_friday_reminder(rep: dict, data: dict, ff_data: dict) -> str:
    prefix = message_prefix()
    oid    = rep["owner_id"]

    critical_deals    = (data["past_due"] + data["no_recent_contact"])[:8]
    critical_contacts = (data["stuck_lead_status"] + data["missing_lead_status"])[:5]

    lines = [
        f"{prefix}*Friday Hygiene Check-In — {rep['name']}*",
        f"Sent by <@{ARI['slack_id']}>  ·  A reminder on open issues from Monday's audit",
        "",
    ]

    if not critical_deals and not critical_contacts:
        lines += [
            "No critical open issues — great work this week.",
            "_Monday's full audit will recap the full pipeline state._",
        ]
        return "\n".join(lines)

    if critical_deals:
        lines.append(f"*{len(data['past_due']) + len(data['no_recent_contact'])} deals still need attention:*")
        for i, d in enumerate(critical_deals, 1):
            if d.get("is_past_due"):
                meta = f"past due {d['close_date_str']}"
            elif d.get("days_since_contact") is None:
                meta = "never contacted"
            else:
                meta = f"{d['days_since_contact']}d no contact"
            lines += _deal_row(i, d, meta)
        lines.append("")

    if critical_contacts:
        lines.append(f"*{len(data['stuck_lead_status']) + len(data['missing_lead_status'])} contacts need lead status update:*")
        for i, c in enumerate(critical_contacts, 1):
            status = c.get("lead_status") or "no status set"
            lines.append(_contact_row(i, c, status.replace("_", " ").title()))
        lines.append("")

    lines.append("_Update these before end of day Friday._")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Public send functions
# -----------------------------------------------------------------------------

def send_scorecard_to_ari(scorecard_rows: list, ff_data: dict) -> bool:
    week_label = _week_label()
    slack_ids  = resolve_slack_ids_for_scorecard()
    mode_label = "[FRIDAY]" if IS_FRIDAY else "[MONDAY]"
    print(f"\n[Slack]{mode_label} Scorecard → {slack_ids}")

    channel_id = _open_dm(slack_ids)
    if not channel_id:
        return False

    ok = _post(channel_id, _format_scorecard(scorecard_rows, week_label))
    print(f"  Scorecard {'sent' if ok else 'FAILED'}.")
    return ok


def send_rep_messages(results: dict, ff_data: dict) -> None:
    week_label = _week_label()
    mode_label = "[FRIDAY]" if IS_FRIDAY else "[MONDAY]"
    print(f"\n[Slack]{mode_label} Sending per-rep DMs...")

    for oid, data in results.items():
        rep       = data["rep"]
        slack_ids = resolve_slack_ids_for_rep(rep)

        print(f"  {rep['name']} → {slack_ids}...")
        channel_id = _open_dm(slack_ids)
        if not channel_id:
            print(f"  FAILED to open DM for {rep['name']}.")
            continue

        text = (
            _format_friday_reminder(rep, data, ff_data)
            if IS_FRIDAY
            else _format_rep_message(rep, data, week_label, ff_data)
        )

        ok = _post(channel_id, text)
        print(f"  {'Sent' if ok else 'FAILED'}.")
        time.sleep(1)
