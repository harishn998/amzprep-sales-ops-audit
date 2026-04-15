# =============================================================================
# slack_client.py — Slack notifications
# New in this version:
#   - AI insight blocks (risk badge + reason + action) per flagged deal
#   - New hygiene sections: stuck lead status (L4), no recent contact (E1),
#     calls without notes (E2), email-sourced deals with no follow-up (D7)
#   - Friday reminder mode: shorter check-in message
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
# AI insight block — appended below each flagged deal
# -----------------------------------------------------------------------------

def _ai_block(deal: dict) -> str | None:
    """
    Returns a formatted AI insight line for a deal, or None if no AI data.
    Risk levels: High → !! prefix, Medium → >, Low → -
    """
    risk   = deal.get("ai_risk")
    reason = deal.get("ai_reason")
    action = deal.get("ai_action")

    if not risk or not reason:
        return None

    risk_label = {"High": "HIGH RISK", "Medium": "MED RISK", "Low": "LOW RISK"}.get(risk, risk)
    lines = [f"     _{risk_label}_ — {reason}"]
    if action:
        lines.append(f"     *Suggested action:* {action}")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Scorecard — Ari's consolidated summary
# -----------------------------------------------------------------------------

def _format_scorecard(scorecard_rows: list, week_label: str) -> str:
    prefix      = message_prefix()
    total_deals = sum(r["open_deals"]        for r in scorecard_rows)
    total_pd    = sum(r["past_due"]          for r in scorecard_rows)
    total_stale = sum(r["stale"]             for r in scorecard_rows)
    total_nc    = sum(r["no_recent_contact"] for r in scorecard_rows)
    total_amt   = sum(r["missing_amount"]    for r in scorecard_rows)
    total_src   = sum(r["missing_source"]    for r in scorecard_rows)
    total_mrr   = sum(r["missing_mrr"]       for r in scorecard_rows)
    total_sta   = sum(r["missing_status"]    for r in scorecard_rows)
    total_lead  = sum(r["missing_lead_status"] for r in scorecard_rows)
    total_stuck = sum(r["stuck_lead_status"] for r in scorecard_rows)
    total_cnotes= sum(r["calls_without_notes"] for r in scorecard_rows)
    total_email = sum(r["email_no_followup"] for r in scorecard_rows)

    lines = [
        f"{prefix}*HubSpot Deal & Lead Hygiene Audit*",
        f"Week of {week_label}  |  {len(scorecard_rows)} Reps  |  {total_deals} Open Deals",
    ]

    if IS_DEV:
        lines.append("_DEV MODE — real HubSpot data, dev team recipients only_")

    lines += [
        "",
        "```",
        f"{'Rep':<14} {'Open':>5} {'PstDue':>7} {'Stale':>6} {'NoCon':>6} {'No$':>5} {'NoSrc':>6} {'NoMRR':>6} {'NoSta':>6} {'NoLead':>7} {'Stuck':>6}",
        "-" * 78,
    ]

    for r in scorecard_rows:
        first = r["name"].split()[0]
        lines.append(
            f"{first:<14} {r['open_deals']:>5} {r['past_due']:>7} "
            f"{r['stale']:>6} {r['no_recent_contact']:>6} {r['missing_amount']:>5} "
            f"{r['missing_source']:>6} {r['missing_mrr']:>6} {r['missing_status']:>6} "
            f"{r['missing_lead_status']:>7} {r['stuck_lead_status']:>6}"
        )

    lines += [
        "-" * 78,
        f"{'TOTAL':<14} {total_deals:>5} {total_pd:>7} {total_stale:>6} "
        f"{total_nc:>6} {total_amt:>5} {total_src:>6} {total_mrr:>6} {total_sta:>6} "
        f"{total_lead:>7} {total_stuck:>6}",
        "```",
        "",
        "*Additional flags:*",
        f"  Calls logged with no notes:       {total_cnotes}",
        f"  Email-sourced deals, no follow-up: {total_email}",
    ]

    lines += ["", "*Fireflies — Previous Week*"]
    for r in scorecard_rows:
        status = "OK" if r["ff_status"] == "OK" else "NO CALLS RECORDED"
        lines.append(f"  {r['name'].split()[0]:<14} {r['ff_count']} transcript(s)   {status}")

    lines += ["", "_Per-rep audit DMs sent below._"]
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Per-rep full weekly message
# -----------------------------------------------------------------------------

def _format_rep_message(rep: dict, data: dict, week_label: str, ff_data: dict) -> str:
    prefix     = message_prefix()
    oid        = rep["owner_id"]
    ff         = ff_data.get(oid, {"count": 0, "status": "NO DATA"})
    open_count = data["open_deals"]

    total_issues = sum([
        len(data["past_due"]),
        len(data["stale"]),
        len(data["no_recent_contact"]),
        len(data["missing_amount"]),
        len(data["missing_source"]),
        len(data["missing_mrr"]),
        len(data["missing_status"]),
        len(data["missing_lead_status"]),
        len(data["stuck_lead_status"]),
        len(data["calls_without_notes"]),
        len(data["created_from_email_no_followup"]),
    ])

    lines = [
        f"{prefix}*HubSpot Pipeline Hygiene Audit*",
        f"Rep: *{rep['name']}*  |  Week of {week_label}  |  Sent by <@{ARI['slack_id']}>",
    ]

    if IS_DEV:
        lines += ["", f"_DEV MODE  |  Prod email: {rep['email']}  |  Owner ID: {rep['owner_id']}_"]

    # ── Summary table ─────────────────────────────────────────────────────────
    lines += [
        "",
        f"*Pipeline Summary — {open_count} open deals*",
        "```",
        f"{'Issue':<32} {'Count':>5}",
        "-" * 39,
        f"{'Past-due close date (2025+)':<32} {len(data['past_due']):>5}",
        f"{'Stale — no CRM activity 14d+':<32} {len(data['stale']):>5}",
        f"{'No contact logged 14d+':<32} {len(data['no_recent_contact']):>5}",
        f"{'Email-sourced, no follow-up':<32} {len(data['created_from_email_no_followup']):>5}",
        f"{'Missing deal amount':<32} {len(data['missing_amount']):>5}",
        f"{'Missing pipeline source':<32} {len(data['missing_source']):>5}",
        f"{'Missing MRR':<32} {len(data['missing_mrr']):>5}",
        f"{'Missing deal status':<32} {len(data['missing_status']):>5}",
        "-" * 39,
        f"{'Deal issues total':<32} {sum([len(data[k]) for k in ['past_due','stale','no_recent_contact','created_from_email_no_followup','missing_amount','missing_source','missing_mrr','missing_status']]):>5}",
        "",
        f"{'Missing lead status':<32} {len(data['missing_lead_status']):>5}",
        f"{'Stuck lead status (7d+)':<32} {len(data['stuck_lead_status']):>5}",
        f"{'Calls with no notes':<32} {len(data['calls_without_notes']):>5}",
        "-" * 39,
        f"{'TOTAL ISSUES':<32} {total_issues:>5}",
        "```",
    ]

    # ── Past-due deals ────────────────────────────────────────────────────────
    if data["past_due"]:
        lines += ["", f"*Past-Due Deals ({len(data['past_due'])} total, oldest first)*"]
        for i, d in enumerate(data["past_due"][:MAX_DEALS_SHOWN], 1):
            date_str = d["close_date_str"] if d["close_date_str"] else "no close date"
            lines.append(f"  {i:>2}. <{d['url']}|{d['name']}> — due {date_str}")
            ai = _ai_block(d)
            if ai:
                lines.append(ai)
        if len(data["past_due"]) > MAX_DEALS_SHOWN:
            lines.append(f"  _...and {len(data['past_due']) - MAX_DEALS_SHOWN} more_")

    # ── No recent contact ─────────────────────────────────────────────────────
    if data["no_recent_contact"]:
        lines += ["", f"*No Contact Logged in 14+ Days ({len(data['no_recent_contact'])} total)*"]
        for i, d in enumerate(data["no_recent_contact"][:MAX_DEALS_SHOWN], 1):
            if d["days_since_contact"] is None:
                contact_str = "never contacted"
            else:
                contact_str = f"{d['days_since_contact']}d since last contact"
            lines.append(f"  {i:>2}. <{d['url']}|{d['name']}> — {contact_str}")
            ai = _ai_block(d)
            if ai:
                lines.append(ai)
        if len(data["no_recent_contact"]) > MAX_DEALS_SHOWN:
            lines.append(f"  _...and {len(data['no_recent_contact']) - MAX_DEALS_SHOWN} more_")

    # ── Stale deals ───────────────────────────────────────────────────────────
    if data["stale"]:
        lines += ["", f"*Stale Deals — No CRM Activity ({len(data['stale'])} total, worst first)*"]
        for i, d in enumerate(data["stale"][:MAX_DEALS_SHOWN], 1):
            activity = (
                "no activity ever"
                if d["days_inactive"] is None
                else f"{d['days_inactive']}d since last activity"
            )
            lines.append(f"  {i:>2}. <{d['url']}|{d['name']}> — {activity}")
            ai = _ai_block(d)
            if ai:
                lines.append(ai)
        if len(data["stale"]) > MAX_DEALS_SHOWN:
            lines.append(f"  _...and {len(data['stale']) - MAX_DEALS_SHOWN} more_")

    # ── Email-sourced, no follow-up ───────────────────────────────────────────
    if data["created_from_email_no_followup"]:
        lines += ["", f"*Deals From Email Thread — No Follow-Up Contact Logged ({len(data['created_from_email_no_followup'])})*"]
        for i, d in enumerate(data["created_from_email_no_followup"][:MAX_DEALS_SHOWN], 1):
            lines.append(f"  {i:>2}. <{d['url']}|{d['name']}>")
            ai = _ai_block(d)
            if ai:
                lines.append(ai)

    # ── Stuck lead status (L4) ────────────────────────────────────────────────
    if data["stuck_lead_status"]:
        lines += ["", f"*Contacts With Stuck Lead Status — 7+ Days ({len(data['stuck_lead_status'])} total)*"]
        for i, c in enumerate(data["stuck_lead_status"][:MAX_CONTACTS_SHOWN], 1):
            days_str = f"{c['days_stuck']}d" if c["days_stuck"] is not None else "unknown"
            status   = c.get("lead_status", "unknown")
            lines.append(f"  {i:>2}. <{c['url']}|{c['name']}> — {status} for {days_str}")
        if len(data["stuck_lead_status"]) > MAX_CONTACTS_SHOWN:
            lines.append(f"  _...and {len(data['stuck_lead_status']) - MAX_CONTACTS_SHOWN} more_")

    # ── Missing lead status (L1) ──────────────────────────────────────────────
    if data["missing_lead_status"]:
        lines += ["", f"*Contacts Missing Lead Status ({len(data['missing_lead_status'])} total)*"]
        for i, c in enumerate(data["missing_lead_status"][:MAX_CONTACTS_SHOWN], 1):
            lines.append(f"  {i:>2}. <{c['url']}|{c['name']}>")
        if len(data["missing_lead_status"]) > MAX_CONTACTS_SHOWN:
            lines.append(f"  _...and {len(data['missing_lead_status']) - MAX_CONTACTS_SHOWN} more_")

    # ── Calls without notes (E2) ──────────────────────────────────────────────
    if data["calls_without_notes"]:
        lines += ["", f"*Calls Logged With No Notes ({len(data['calls_without_notes'])} total)*"]
        for i, c in enumerate(data["calls_without_notes"][:MAX_CONTACTS_SHOWN], 1):
            lines.append(f"  {i:>2}. {c['title']}")

    # ── Fireflies ─────────────────────────────────────────────────────────────
    ff_str = (
        f"{ff['count']} transcript(s) recorded — OK"
        if ff["status"] == "OK"
        else "No transcripts recorded — check Fireflies calendar connection"
    )
    lines += ["", "*Fireflies — Previous Week*", f"  {ff_str}"]

    # ── Action items ──────────────────────────────────────────────────────────
    lines += [
        "",
        "*Action Items This Week*",
        "  1. Update or close-lost any deals with past-due close dates",
        "  2. Log a call, email, or note on every deal with no recent contact",
        "  3. Fill in Deal Amount, Pipeline Source, MRR, and Deal Status",
        "  4. For email-sourced deals — log your first call or email engagement",
        "  5. Advance or close any contacts stuck in Attempted to Contact / Open",
        "  6. Add outcome notes to any calls logged without them",
        "  7. Assign Lead Status to all contacts without one",
        "",
        "_All unresolved issues carry forward every week until fixed._",
    ]

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Friday reminder — shorter mid-week check-in
# -----------------------------------------------------------------------------

def _format_friday_reminder(rep: dict, data: dict, ff_data: dict) -> str:
    """Shorter Friday message — just critical open items, no full scorecard."""
    prefix     = message_prefix()
    oid        = rep["owner_id"]

    critical_deals    = data["past_due"] + data["no_recent_contact"]
    critical_contacts = data["stuck_lead_status"] + data["missing_lead_status"]

    lines = [
        f"{prefix}*Friday Hygiene Check-In — {rep['name']}*",
        f"Sent by <@{ARI['slack_id']}> — a quick reminder on open issues from Monday's audit.",
        "",
    ]

    if not critical_deals and not critical_contacts:
        lines += [
            "No critical open issues found — great work this week.",
            "Monday's full audit will recap the pipeline state.",
        ]
        return "\n".join(lines)

    if critical_deals:
        lines.append(f"*{len(critical_deals)} deals still need attention:*")
        for i, d in enumerate(critical_deals[:6], 1):
            if d.get("is_past_due"):
                tag = f"past due {d['close_date_str']}"
            elif d.get("days_since_contact") is None:
                tag = "never contacted"
            else:
                tag = f"{d['days_since_contact']}d no contact"
            lines.append(f"  {i:>2}. <{d['url']}|{d['name']}> — {tag}")
            ai = _ai_block(d)
            if ai:
                lines.append(ai)
        lines.append("")

    if critical_contacts:
        lines.append(f"*{len(critical_contacts)} contacts need lead status update:*")
        for i, c in enumerate(critical_contacts[:5], 1):
            status = c.get("lead_status") or "no status"
            lines.append(f"  {i:>2}. <{c['url']}|{c['name']}> — {status}")
        lines.append("")

    lines += [
        "_Update these before end of day. Monday's audit will track carry-forwards._",
    ]

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

        if IS_FRIDAY:
            text = _format_friday_reminder(rep, data, ff_data)
        else:
            text = _format_rep_message(rep, data, week_label, ff_data)

        ok = _post(channel_id, text)
        print(f"  {'Sent' if ok else 'FAILED'}.")
        time.sleep(1)
