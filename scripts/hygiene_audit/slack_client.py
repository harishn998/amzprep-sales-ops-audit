# =============================================================================
# slack_client.py — Slack notifications
# Design: card-per-deal format with clean visual separation
# Each deal is a self-contained block: name + key stat + AI insight
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
DIVIDER            = "━" * 44


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
        print(f"  [Slack] conversations.open failed: {data.get('error')}")
        return None
    return data["channel"]["id"]


# Bot display name and icon — overrides workspace-cached settings.
# This ensures messages always show as 'Kiro' regardless of reinstall status.
BOT_NAME     = "Kiro"
BOT_ICON_URL = "https://amzprep.com/favicon.ico"  # swap for AMZ logo URL if available


def _post(channel_id: str, text: str) -> bool:
    resp = requests.post(
        f"{SLACK_API}/chat.postMessage",
        json={
            "channel":  channel_id,
            "text":     text,
            "mrkdwn":  True,
            "username": BOT_NAME,      # forces display name = Kiro
        },
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
# Deal card renderer
# Each deal occupies a consistent 2–3 line block:
#   Line 1:  N.  *[RISK]*  Deal Name (link)  ·  key stat
#   Line 2:  (indented) Reason: ...
#   Line 3:  (indented) Action: ...
# -----------------------------------------------------------------------------

def _risk_badge(deal: dict) -> str:
    risk = deal.get("ai_risk")
    if not risk:
        return ""
    labels = {"High": "*[HIGH]*", "Medium": "*[MED]*", "Low": "*[LOW]*"}
    return labels.get(risk, "") + "  "


def _deal_card(n: int, deal: dict, stat: str) -> list:
    badge = _risk_badge(deal)
    name_link = f"<{deal['url']}|{deal['name']}>"
    lines = [f"  {n:>2}.  {badge}{name_link}  ·  {stat}"]

    reason = deal.get("ai_reason")
    action = deal.get("ai_action")
    if reason:
        lines.append(f"       _Reason:_ {reason}")
    if action:
        lines.append(f"       _Next step:_ *{action}*")
    return lines


def _contact_card(n: int, c: dict, meta: str = "") -> str:
    suffix = f"  ·  {meta}" if meta else ""
    return f"  {n:>2}.  <{c['url']}|{c['name']}>{suffix}"


def _overflow(total: int, shown: int) -> list:
    if total > shown:
        return [f"  _+ {total - shown} more — open HubSpot to view all_"]
    return []


def _section(title: str) -> list:
    return ["", f"*{title}*", DIVIDER]


# -----------------------------------------------------------------------------
# Scorecard — Ari's consolidated summary (two tables)
# -----------------------------------------------------------------------------

def _format_scorecard(rows: list, week_label: str) -> str:
    prefix = message_prefix()
    totals = {
        "open":   sum(r["open_deals"]          for r in rows),
        "pd":     sum(r["past_due"]            for r in rows),
        "stale":  sum(r["stale"]               for r in rows),
        "nc":     sum(r["no_recent_contact"]   for r in rows),
        "amt":    sum(r["missing_amount"]      for r in rows),
        "src":    sum(r["missing_source"]      for r in rows),
        "mrr":    sum(r["missing_mrr"]         for r in rows),
        "sta":    sum(r["missing_status"]      for r in rows),
        "lead":   sum(r["missing_lead_status"] for r in rows),
        "stuck":  sum(r["stuck_lead_status"]   for r in rows),
        "cnotes": sum(r["calls_without_notes"] for r in rows),
    }

    lines = [
        f"{prefix}*HubSpot Hygiene Audit — Weekly Scorecard*",
        f"Week of {week_label}  |  {len(rows)} Reps  |  {totals['open']} Open Deals",
    ]
    if IS_DEV:
        lines.append("_DEV MODE — real HubSpot data, dev team only_")

    # Table 1 — Deal Issues
    lines += [
        "",
        "*Deal Issues*",
        "```",
        f"{'Rep':<10} {'Open':>5} {'PstDue':>7} {'Stale':>6} {'NoCon':>6} {'No$':>5} {'NoSrc':>6} {'NoMRR':>6} {'NoSta':>6}",
        "─" * 63,
    ]
    for r in rows:
        first = r["name"].split()[0]
        lines.append(
            f"{first:<10} {r['open_deals']:>5} {r['past_due']:>7} "
            f"{r['stale']:>6} {r['no_recent_contact']:>6} {r['missing_amount']:>5} "
            f"{r['missing_source']:>6} {r['missing_mrr']:>6} {r['missing_status']:>6}"
        )
    lines += [
        "─" * 63,
        f"{'TOTAL':<10} {totals['open']:>5} {totals['pd']:>7} {totals['stale']:>6} "
        f"{totals['nc']:>6} {totals['amt']:>5} {totals['src']:>6} {totals['mrr']:>6} {totals['sta']:>6}",
        "```",
    ]

    # Table 2 — Contact & Activity Issues
    lines += [
        "",
        "*Contact & Activity Issues*",
        "```",
        f"{'Rep':<10} {'NoLead':>7} {'Stuck7d':>8} {'CallsNoNote':>12}",
        "─" * 40,
    ]
    for r in rows:
        first = r["name"].split()[0]
        lines.append(
            f"{first:<10} {r['missing_lead_status']:>7} {r['stuck_lead_status']:>8} "
            f"{r['calls_without_notes']:>12}"
        )
    lines += [
        "─" * 40,
        f"{'TOTAL':<10} {totals['lead']:>7} {totals['stuck']:>8} {totals['cnotes']:>12}",
        "```",
        "_Stuck = contacts in Attempted to Contact or In Progress with no update in 7+ days_",
    ]

    # Fireflies
    lines += ["", "*Fireflies — Previous Week*", "```"]
    for r in rows:
        status = "OK" if r["ff_status"] == "OK" else "NO CALLS RECORDED"
        lines.append(f"  {r['name'].split()[0]:<10}  {r['ff_count']:>2} transcript(s)   {status}")
    lines += ["```", "", "_Per-rep audit messages sent below._"]

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Per-rep weekly message
# -----------------------------------------------------------------------------

def _format_rep_message(rep: dict, data: dict, week_label: str, ff_data: dict) -> str:
    prefix     = message_prefix()
    oid        = rep["owner_id"]
    ff         = ff_data.get(oid, {"count": 0, "status": "NO DATA"})
    open_count = data["open_deals"]

    pd   = data["past_due"]
    nc   = data["no_recent_contact"]
    st   = data["stale"]
    ef   = data["created_from_email_no_followup"]
    ml   = data["missing_lead_status"]
    sk   = data.get("stuck_lead_status", [])
    cn   = data.get("calls_without_notes", [])

    deal_total = sum(len(data[k]) for k in [
        "past_due","stale","no_recent_contact","created_from_email_no_followup",
        "missing_amount","missing_source","missing_mrr","missing_status",
    ])
    grand_total = deal_total + len(ml) + len(sk) + len(cn)

    lines = [
        f"{prefix}*HubSpot Pipeline Hygiene Audit*",
        f"*{rep['name']}*  ·  Week of {week_label}  ·  Sent by <@{ARI['slack_id']}>",
    ]
    if IS_DEV:
        lines += ["", f"_DEV MODE  ·  Prod email: {rep['email']}_"]

    # ── Summary table (code block — monospace aligned) ─────────────────────
    lines += [
        "",
        f"*Weekly Summary — {open_count} open deals*",
        "```",
        f"{'Category':<32} {'Count':>5}",
        "─" * 39,
        f"{'Past-due close date (2025+)':<32} {len(pd):>5}",
        f"{'Stale  (no CRM activity 14d+)':<32} {len(st):>5}",
        f"{'No contact logged 14d+':<32} {len(nc):>5}",
        f"{'Email-sourced, no follow-up':<32} {len(ef):>5}",
        f"{'Missing deal amount':<32} {len(data['missing_amount']):>5}",
        f"{'Missing pipeline source':<32} {len(data['missing_source']):>5}",
        f"{'Missing MRR':<32} {len(data['missing_mrr']):>5}",
        f"{'Missing deal status':<32} {len(data['missing_status']):>5}",
        "─" * 39,
        f"{'Deal issues total':<32} {deal_total:>5}",
        "",
        f"{'Missing lead status':<32} {len(ml):>5}",
        f"{'Stuck in open status (7d+)':<32} {len(sk):>5}",
        f"{'Calls with no notes (30d)':<32} {len(cn):>5}",
        "─" * 39,
        f"{'TOTAL ISSUES':<32} {grand_total:>5}",
        "```",
    ]

    # ── Past-due deal cards ─────────────────────────────────────────────────
    if pd:
        lines += _section(f"Past-Due Deals  ({len(pd)} total, oldest first)")
        for i, d in enumerate(pd[:MAX_DEALS_SHOWN], 1):
            stat = f"due {d['close_date_str']}" if d["close_date_str"] else "no close date set"
            lines += _deal_card(i, d, stat)
            if i < min(len(pd), MAX_DEALS_SHOWN):
                lines.append("  " + "·" * 36)
        lines += _overflow(len(pd), MAX_DEALS_SHOWN)

    # ── No recent contact cards ─────────────────────────────────────────────
    if nc:
        lines += _section(f"No Contact Logged in 14+ Days  ({len(nc)} total)")
        for i, d in enumerate(nc[:MAX_DEALS_SHOWN], 1):
            stat = (
                "never contacted"
                if d["days_since_contact"] is None
                else f"{d['days_since_contact']}d since last contact"
            )
            lines += _deal_card(i, d, stat)
            if i < min(len(nc), MAX_DEALS_SHOWN):
                lines.append("  " + "·" * 36)
        lines += _overflow(len(nc), MAX_DEALS_SHOWN)

    # ── Stale deal cards ────────────────────────────────────────────────────
    if st:
        lines += _section(f"Stale Deals — No CRM Activity  ({len(st)} total)")
        for i, d in enumerate(st[:MAX_DEALS_SHOWN], 1):
            stat = (
                "no activity ever"
                if d["days_inactive"] is None
                else f"{d['days_inactive']}d inactive"
            )
            lines += _deal_card(i, d, stat)
            if i < min(len(st), MAX_DEALS_SHOWN):
                lines.append("  " + "·" * 36)
        lines += _overflow(len(st), MAX_DEALS_SHOWN)

    # ── Email-sourced no follow-up ──────────────────────────────────────────
    if ef:
        lines += _section(f"Email-Sourced Deals — No Follow-Up  ({len(ef)} total)")
        for i, d in enumerate(ef[:MAX_DEALS_SHOWN], 1):
            lines += _deal_card(i, d, "from email  ·  no contact logged")
        lines += _overflow(len(ef), MAX_DEALS_SHOWN)

    # ── Stuck lead status ───────────────────────────────────────────────────
    if sk:
        lines += _section(f"Contacts Stuck in Lead Status  ({len(sk)} total, 7d+)")
        lines.append("_Attempted to Contact or In Progress with no update_")
        for i, c in enumerate(sk[:MAX_CONTACTS_SHOWN], 1):
            days_str = f"{c['days_stuck']}d" if c.get("days_stuck") is not None else "?"
            status   = (c.get("lead_status") or "").replace("_", " ").title()
            lines.append(_contact_card(i, c, f"{status}  ·  stuck {days_str}"))
        lines += _overflow(len(sk), MAX_CONTACTS_SHOWN)

    # ── Missing lead status ─────────────────────────────────────────────────
    if ml:
        lines += _section(f"Contacts Missing Lead Status  ({len(ml)} total)")
        for i, c in enumerate(ml[:MAX_CONTACTS_SHOWN], 1):
            lines.append(_contact_card(i, c))
        lines += _overflow(len(ml), MAX_CONTACTS_SHOWN)

    # ── Calls without notes ─────────────────────────────────────────────────
    if cn:
        lines += _section(f"Calls Logged With No Notes  ({len(cn)} in last 30 days)")
        for i, c in enumerate(cn[:MAX_CONTACTS_SHOWN], 1):
            lines.append(f"  {i:>2}.  {c['title']}")
        lines += _overflow(len(cn), MAX_CONTACTS_SHOWN)

    # ── Fireflies ───────────────────────────────────────────────────────────
    ff_str = (
        f"{ff['count']} transcript(s) recorded this week — OK"
        if ff["status"] == "OK"
        else "No transcripts found — check Fireflies is connected to your calendar"
    )
    lines += ["", DIVIDER, f"*Fireflies*  ·  {ff_str}"]

    # ── Action items ────────────────────────────────────────────────────────
    lines += [
        "",
        DIVIDER,
        "*Your Action Items This Week*",
        "  1.  Close-lost or update any deal with a past-due close date",
        "  2.  Log a call, email, or note on every deal with no recent contact",
        "  3.  Fill in Deal Amount, Pipeline Source, MRR, and Deal Status on all open deals",
        "  4.  For email-sourced deals — log your first contact in HubSpot",
        "  5.  Advance or close contacts stuck in Attempted to Contact or In Progress",
        "  6.  Add outcome notes to any calls logged without them",
        "  7.  Assign Lead Status to all contacts that are missing one",
        "",
        "_Unresolved issues carry forward every week until fixed._",
    ]

    return "\n".join(line for line in lines if line is not None)


# -----------------------------------------------------------------------------
# Friday reminder
# -----------------------------------------------------------------------------

def _format_friday_reminder(rep: dict, data: dict, ff_data: dict) -> str:
    prefix = message_prefix()
    critical_deals    = (data["past_due"] + data["no_recent_contact"])[:6]
    critical_contacts = (data.get("stuck_lead_status", []) + data["missing_lead_status"])[:4]

    lines = [
        f"{prefix}*Friday Check-In — {rep['name']}*",
        f"Reminder on open items from Monday's audit  ·  Sent by <@{ARI['slack_id']}>",
        "",
    ]

    if not critical_deals and not critical_contacts:
        lines += ["No critical open issues this week — great work.", "_Full audit on Monday._"]
        return "\n".join(lines)

    if critical_deals:
        total = len(data["past_due"]) + len(data["no_recent_contact"])
        lines += _section(f"{total} Deals Still Need Attention")
        for i, d in enumerate(critical_deals, 1):
            if d.get("is_past_due"):
                stat = f"past due {d['close_date_str']}"
            elif d.get("days_since_contact") is None:
                stat = "never contacted"
            else:
                stat = f"{d['days_since_contact']}d no contact"
            lines += _deal_card(i, d, stat)
            if i < len(critical_deals):
                lines.append("  " + "·" * 36)
        lines.append("")

    if critical_contacts:
        total_c = len(data.get("stuck_lead_status", [])) + len(data["missing_lead_status"])
        lines += _section(f"{total_c} Contacts Need Lead Status Update")
        for i, c in enumerate(critical_contacts, 1):
            status = (c.get("lead_status") or "no status").replace("_", " ").title()
            lines.append(_contact_card(i, c, status))
        lines.append("")

    lines.append("_Please update these before end of day._")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Public send functions
# -----------------------------------------------------------------------------

def send_scorecard_to_ari(scorecard_rows: list, ff_data: dict) -> bool:
    week_label = _week_label()
    slack_ids  = resolve_slack_ids_for_scorecard()
    mode       = "[FRIDAY]" if IS_FRIDAY else "[MONDAY]"
    print(f"\n[Slack]{mode} Scorecard → {slack_ids}")

    channel_id = _open_dm(slack_ids)
    if not channel_id:
        return False

    ok = _post(channel_id, _format_scorecard(scorecard_rows, week_label))
    print(f"  Scorecard {'sent' if ok else 'FAILED'}.")
    return ok


def send_rep_messages(results: dict, ff_data: dict) -> None:
    week_label = _week_label()
    mode       = "[FRIDAY]" if IS_FRIDAY else "[MONDAY]"
    print(f"\n[Slack]{mode} Sending per-rep DMs...")

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
