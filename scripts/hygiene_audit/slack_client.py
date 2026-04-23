# =============================================================================
# slack_client.py — Slack notifications (Block Kit format)
# =============================================================================
# Uses Slack Block Kit for clean, professional, card-style messages.
# Block Kit renders with proper visual hierarchy — headers, dividers,
# sections — instead of plain markdown text.
#
# Message types:
#   Monday    → full audit report per rep + scorecard to Ari
#   Friday    → lighter check-in with only critical open items
#   Daily SLA → breach alerts (handled by sla_notifier.py)
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
MAX_DEALS_SHOWN    = 6
MAX_CONTACTS_SHOWN = 6
BOT_NAME           = "Kiro"


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


def _post_blocks(channel_id: str, blocks: list, fallback_text: str) -> bool:
    """Send a Block Kit message. fallback_text shown in notifications."""
    resp = requests.post(
        f"{SLACK_API}/chat.postMessage",
        json={
            "channel":  channel_id,
            "username": BOT_NAME,
            "text":     fallback_text,   # notification preview
            "blocks":   blocks,
        },
        headers=_headers(),
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"  [Slack] chat.postMessage failed: {data.get('error')}")
        return False
    return True


# =============================================================================
# Block Kit builders — clean composable primitives
# =============================================================================

def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": False}}

def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}

def _divider() -> dict:
    return {"type": "divider"}

def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}

def _fields(*field_texts) -> dict:
    """Two-column field block. Max 10 fields."""
    return {
        "type": "section",
        "fields": [{"type": "mrkdwn", "text": t} for t in field_texts],
    }


def _week_label() -> str:
    today       = datetime.now(tz=timezone.utc)
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    if last_monday.month == last_sunday.month:
        return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%-d, %Y')}"
    return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%B %-d, %Y')}"


def _risk_tag(deal: dict) -> str:
    risk = deal.get("ai_risk")
    return {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(risk, "⚪") if risk else ""


def _deal_block(n: int, deal: dict, stat: str) -> list:
    """One deal rendered as a rich section with optional AI insight."""
    tag       = _risk_tag(deal)
    name_link = f"<{deal['url']}|{deal['name']}>"
    header    = f"*{n}.  {tag}  {name_link}*"
    detail    = f"_{stat}_"

    reason = deal.get("ai_reason")
    action = deal.get("ai_action")

    text = f"{header}\n{detail}"
    if reason:
        text += f"\n>*Why:* {reason}"
    if action:
        text += f"\n>*Do now:* {action}"

    return [_section(text)]


def _contact_block(n: int, c: dict, meta: str = "") -> str:
    link   = f"<{c['url']}|{c['name']}>"
    suffix = f"  —  _{meta}_" if meta else ""
    return f"*{n}.* {link}{suffix}"


def _overflow_context(total: int, shown: int) -> list:
    if total > shown:
        return [_context(f"_+ {total - shown} more — <https://app.hubspot.com/contacts/878268/deals|open HubSpot to see all>_")]
    return []


# =============================================================================
# Monday scorecard (Ari's summary)
# =============================================================================

def _build_scorecard_blocks(rows: list, week_label: str) -> list:
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

    blocks = [
        _header("HubSpot Hygiene Audit — Weekly Scorecard"),
        _section(
            f"*Week of {week_label}*  ·  {len(rows)} reps  ·  *{totals['open']} open deals*"
            + ("\n_DEV MODE — real data, dev team recipients only_" if IS_DEV else "")
        ),
        _divider(),
    ]

    # ── Deal issues table ──────────────────────────────────────────────────
    blocks.append(_section("*Deal Issues by Rep*"))

    table_lines = [
        "```",
        f"{'Rep':<10} {'Open':>5} {'PstDue':>7} {'Stale':>6} {'NoCon':>6} {'No$':>5} {'NoSrc':>6} {'NoMRR':>6} {'NoSta':>6}",
        "─" * 63,
    ]
    for r in rows:
        name = r["name"].split()[0]
        table_lines.append(
            f"{name:<10} {r['open_deals']:>5} {r['past_due']:>7} "
            f"{r['stale']:>6} {r['no_recent_contact']:>6} {r['missing_amount']:>5} "
            f"{r['missing_source']:>6} {r['missing_mrr']:>6} {r['missing_status']:>6}"
        )
    table_lines += [
        "─" * 63,
        f"{'TOTAL':<10} {totals['open']:>5} {totals['pd']:>7} {totals['stale']:>6} "
        f"{totals['nc']:>6} {totals['amt']:>5} {totals['src']:>6} {totals['mrr']:>6} {totals['sta']:>6}",
        "```",
    ]
    blocks.append(_section("\n".join(table_lines)))

    # ── Contact & activity issues ──────────────────────────────────────────
    blocks.append(_section("*Contact & Activity Issues*"))

    contact_lines = [
        "```",
        f"{'Rep':<10} {'NoLead':>7} {'Stuck7d':>8} {'CallsNoNote':>12}",
        "─" * 40,
    ]
    for r in rows:
        name = r["name"].split()[0]
        contact_lines.append(
            f"{name:<10} {r['missing_lead_status']:>7} {r['stuck_lead_status']:>8} "
            f"{r['calls_without_notes']:>12}"
        )
    contact_lines += [
        "─" * 40,
        f"{'TOTAL':<10} {totals['lead']:>7} {totals['stuck']:>8} {totals['cnotes']:>12}",
        "```",
    ]
    blocks.append(_section("\n".join(contact_lines)))
    blocks.append(_context("_Stuck = contacts in Attempted to Contact or In Progress with no update in 7+ days_"))
    blocks.append(_divider())

    # ── Fireflies ──────────────────────────────────────────────────────────
    blocks.append(_section("*Fireflies — Previous Week*"))
    ff_lines = []
    for r in rows:
        name   = r["name"].split()[0]
        status = "✅  OK" if r["ff_status"] == "OK" else "❌  No calls recorded"
        ff_lines.append(f"`{name:<12}`  {r['ff_count']} transcript(s)   {status}")
    blocks.append(_section("\n".join(ff_lines)))
    blocks.append(_divider())
    blocks.append(_context("_Per-rep audit DMs sent below._"))

    return blocks


# =============================================================================
# Monday per-rep message
# =============================================================================

def _build_rep_blocks(rep: dict, data: dict, week_label: str, ff_data: dict) -> list:
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

    dev_note = f"\n_DEV MODE  ·  Prod email: {rep['email']}_" if IS_DEV else ""

    blocks = [
        _header(f"Pipeline Hygiene Report — {rep['name']}"),
        _section(
            f"*Week of {week_label}*  ·  {open_count} open deals  ·  <@{ARI['slack_id']}>"
            + dev_note
        ),
        _divider(),
    ]

    # ── KPI summary ────────────────────────────────────────────────────────
    blocks.append(_section("*Weekly Summary*"))
    summary_lines = [
        "```",
        f"{'Issue':<32} {'Count':>5}",
        "─" * 39,
        f"{'Past-due close date (2025+)':<32} {len(pd):>5}",
        f"{'Stale — no CRM activity 14d+':<32} {len(st):>5}",
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
    blocks.append(_section("\n".join(summary_lines)))

    # ── Past-due deals ─────────────────────────────────────────────────────
    if pd:
        blocks.append(_divider())
        blocks.append(_section(f"🔴  *Past-Due Deals*  ·  {len(pd)} total, oldest first"))
        for i, d in enumerate(pd[:MAX_DEALS_SHOWN], 1):
            stat = f"due {d['close_date_str']}" if d["close_date_str"] else "no close date set"
            blocks += _deal_block(i, d, stat)
        blocks += _overflow_context(len(pd), MAX_DEALS_SHOWN)

    # ── No recent contact ──────────────────────────────────────────────────
    if nc:
        blocks.append(_divider())
        blocks.append(_section(f"🟠  *No Contact Logged in 14+ Days*  ·  {len(nc)} total"))
        for i, d in enumerate(nc[:MAX_DEALS_SHOWN], 1):
            stat = "never contacted" if d["days_since_contact"] is None else f"{d['days_since_contact']}d since last contact"
            blocks += _deal_block(i, d, stat)
        blocks += _overflow_context(len(nc), MAX_DEALS_SHOWN)

    # ── Stale deals ────────────────────────────────────────────────────────
    if st:
        blocks.append(_divider())
        blocks.append(_section(f"🟡  *Stale Deals — No CRM Activity*  ·  {len(st)} total"))
        for i, d in enumerate(st[:MAX_DEALS_SHOWN], 1):
            stat = "no activity ever" if d["days_inactive"] is None else f"{d['days_inactive']}d inactive"
            blocks += _deal_block(i, d, stat)
        blocks += _overflow_context(len(st), MAX_DEALS_SHOWN)

    # ── Email-sourced no follow-up ─────────────────────────────────────────
    if ef:
        blocks.append(_divider())
        blocks.append(_section(f"📧  *Email-Sourced Deals — No Follow-Up*  ·  {len(ef)} total"))
        for i, d in enumerate(ef[:MAX_DEALS_SHOWN], 1):
            blocks += _deal_block(i, d, "came from email — no contact logged")
        blocks += _overflow_context(len(ef), MAX_DEALS_SHOWN)

    # ── Stuck lead status ──────────────────────────────────────────────────
    if sk:
        blocks.append(_divider())
        blocks.append(_section(f"⏸  *Contacts Stuck in Lead Status*  ·  {len(sk)} total, 7d+"))
        blocks.append(_context("_Attempted to Contact or In Progress with no update_"))
        contact_lines = []
        for i, c in enumerate(sk[:MAX_CONTACTS_SHOWN], 1):
            days_str = f"{c['days_stuck']}d" if c.get("days_stuck") is not None else "?"
            status   = (c.get("lead_status") or "").replace("_", " ").title()
            contact_lines.append(_contact_block(i, c, f"{status}  ·  stuck {days_str}"))
        blocks.append(_section("\n".join(contact_lines)))
        blocks += _overflow_context(len(sk), MAX_CONTACTS_SHOWN)

    # ── Missing lead status ────────────────────────────────────────────────
    if ml:
        blocks.append(_divider())
        blocks.append(_section(f"❓  *Contacts Missing Lead Status*  ·  {len(ml)} total"))
        contact_lines = [_contact_block(i, c) for i, c in enumerate(ml[:MAX_CONTACTS_SHOWN], 1)]
        blocks.append(_section("\n".join(contact_lines)))
        blocks += _overflow_context(len(ml), MAX_CONTACTS_SHOWN)

    # ── Calls without notes ────────────────────────────────────────────────
    if cn:
        blocks.append(_divider())
        blocks.append(_section(f"📞  *Calls Logged With No Notes*  ·  {len(cn)} in last 30 days"))
        call_lines = [f"*{i}.* {c['title']}" for i, c in enumerate(cn[:MAX_CONTACTS_SHOWN], 1)]
        blocks.append(_section("\n".join(call_lines)))
        blocks += _overflow_context(len(cn), MAX_CONTACTS_SHOWN)

    # ── Fireflies ──────────────────────────────────────────────────────────
    blocks.append(_divider())
    ff_str = (
        f"✅  {ff['count']} transcript(s) recorded this week"
        if ff["status"] == "OK"
        else "❌  No transcripts found — check Fireflies is connected to your calendar"
    )
    blocks.append(_section(f"*Fireflies*  ·  {ff_str}"))

    # ── Action items ───────────────────────────────────────────────────────
    blocks.append(_divider())
    blocks.append(_section(
        "*Your Action Items This Week*\n"
        "1.  Close-lost or update any deal with a past-due close date\n"
        "2.  Log a call, email, or note on every deal with no recent contact\n"
        "3.  Fill in Deal Amount, Pipeline Source, MRR, and Deal Status\n"
        "4.  For email-sourced deals — log your first contact in HubSpot\n"
        "5.  Advance or close contacts stuck in Attempted to Contact / In Progress\n"
        "6.  Add outcome notes to any calls logged without them\n"
        "7.  Assign Lead Status to all contacts that are missing one"
    ))
    blocks.append(_context("_Unresolved issues carry forward every week until fixed._"))

    return blocks


# =============================================================================
# Friday reminder blocks
# =============================================================================

def _build_friday_blocks(rep: dict, data: dict, ff_data: dict) -> list:
    pd = data["past_due"]
    nc = data["no_recent_contact"]
    sk = data.get("stuck_lead_status", [])
    ml = data["missing_lead_status"]

    critical_deals    = (pd + nc)[:6]
    critical_contacts = (sk + ml)[:5]

    dev_note = f"\n_DEV MODE  ·  Prod: {rep['email']}_" if IS_DEV else ""

    blocks = [
        _header(f"Friday Check-In — {rep['name']}"),
        _section(
            f"A reminder on open items from Monday's audit  ·  <@{ARI['slack_id']}>"
            + dev_note
        ),
        _divider(),
    ]

    if not critical_deals and not critical_contacts:
        blocks.append(_section("✅  *No critical open issues this week — great work.*"))
        blocks.append(_context("_Full audit on Monday._"))
        return blocks

    if critical_deals:
        total = len(pd) + len(nc)
        blocks.append(_section(f"🔴  *{total} Deals Still Need Attention*"))
        for i, d in enumerate(critical_deals, 1):
            if d.get("is_past_due"):
                stat = f"past due {d['close_date_str']}"
            elif d.get("days_since_contact") is None:
                stat = "never contacted"
            else:
                stat = f"{d['days_since_contact']}d no contact"
            blocks += _deal_block(i, d, stat)

    if critical_contacts:
        blocks.append(_divider())
        total_c = len(sk) + len(ml)
        blocks.append(_section(f"⚠️  *{total_c} Contacts Need Lead Status Update*"))
        contact_lines = []
        for i, c in enumerate(critical_contacts, 1):
            status = (c.get("lead_status") or "no status").replace("_", " ").title()
            contact_lines.append(_contact_block(i, c, status))
        blocks.append(_section("\n".join(contact_lines)))

    blocks.append(_divider())
    blocks.append(_context("_Please update these before end of day Friday._"))
    return blocks


# =============================================================================
# Public send functions
# =============================================================================

def send_scorecard_to_ari(scorecard_rows: list, ff_data: dict) -> bool:
    week_label = _week_label()
    slack_ids  = resolve_slack_ids_for_scorecard()
    mode       = "[FRIDAY]" if IS_FRIDAY else "[MONDAY]"
    print(f"\n[Slack]{mode} Scorecard → {slack_ids}")

    channel_id = _open_dm(slack_ids)
    if not channel_id:
        return False

    blocks   = _build_scorecard_blocks(scorecard_rows, week_label)
    fallback = f"Kiro — Weekly Scorecard — Week of {week_label}"
    ok       = _post_blocks(channel_id, blocks, fallback)
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

        if IS_FRIDAY:
            blocks   = _build_friday_blocks(rep, data, ff_data)
            fallback = f"Kiro — Friday Check-In — {rep['name']}"
        else:
            blocks   = _build_rep_blocks(rep, data, week_label, ff_data)
            fallback = f"Kiro — Pipeline Hygiene Report — {rep['name']} — Week of {week_label}"

        ok = _post_blocks(channel_id, blocks, fallback)
        print(f"  {'Sent' if ok else 'FAILED'}.")
        time.sleep(1)
