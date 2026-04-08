# =============================================================================
# slack_client.py — Slack notifications
# =============================================================================

import os
import time
import requests
from datetime import datetime, timezone, timedelta
from config import (
    ARI, REPS, IS_DEV,
    resolve_slack_ids_for_rep,
    resolve_slack_ids_for_scorecard,
    message_prefix,
)

SLACK_API          = "https://slack.com/api"
MAX_PAST_DUE_SHOWN = 10
MAX_STALE_SHOWN    = 10
MAX_CONTACTS_SHOWN = 10


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
        "Content-Type":  "application/json",
    }


def _open_dm(user_ids: list[str]) -> str | None:
    """
    Open a group DM with the given user IDs.
    Works for 1-person DMs and multi-person group DMs.
    Returns the channel ID or None on failure.
    """
    # Deduplicate while preserving order
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


# -----------------------------------------------------------------------------
# Date helper
# -----------------------------------------------------------------------------

def _week_label() -> str:
    today        = datetime.now(tz=timezone.utc)
    last_monday  = today - timedelta(days=today.weekday() + 7)
    last_sunday  = last_monday + timedelta(days=6)
    if last_monday.month == last_sunday.month:
        return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%-d, %Y')}"
    return f"{last_monday.strftime('%B %-d')} – {last_sunday.strftime('%B %-d, %Y')}"


# -----------------------------------------------------------------------------
# Scorecard — consolidated summary
# -----------------------------------------------------------------------------

def _format_scorecard(scorecard_rows: list, week_label: str) -> str:
    prefix      = message_prefix()
    total_deals = sum(r["open_deals"]          for r in scorecard_rows)
    total_pd    = sum(r["past_due"]            for r in scorecard_rows)
    total_stale = sum(r["stale"]               for r in scorecard_rows)
    total_amt   = sum(r["missing_amount"]      for r in scorecard_rows)
    total_src   = sum(r["missing_source"]      for r in scorecard_rows)
    total_mrr   = sum(r["missing_mrr"]         for r in scorecard_rows)
    total_sta   = sum(r["missing_status"]      for r in scorecard_rows)
    total_lead  = sum(r["missing_lead_status"] for r in scorecard_rows)

    lines = [
        f"{prefix}*HubSpot Deal & Lead Hygiene Audit*",
        f"Week of {week_label} | {len(scorecard_rows)} Reps | {total_deals} Open Deals",
    ]

    if IS_DEV:
        lines.append("_:warning: DEV MODE — real HubSpot data, dev team recipients only_")

    lines += [
        "",
        "```",
        f"{'Rep':<18} {'Open':>5} {'PastDue':>7} {'Stale':>6} {'No$':>5} {'NoSrc':>6} {'NoMRR':>6} {'NoSta':>6} {'NoLead':>7}",
        "-" * 70,
    ]

    for r in scorecard_rows:
        first = r["name"].split()[0]
        lines.append(
            f"{first:<18} {r['open_deals']:>5} {r['past_due']:>7} "
            f"{r['stale']:>6} {r['missing_amount']:>5} {r['missing_source']:>6} "
            f"{r['missing_mrr']:>6} {r['missing_status']:>6} {r['missing_lead_status']:>7}"
        )

    lines += [
        "-" * 70,
        f"{'TOTAL':<18} {total_deals:>5} {total_pd:>7} {total_stale:>6} "
        f"{total_amt:>5} {total_src:>6} {total_mrr:>6} {total_sta:>6} {total_lead:>7}",
        "```",
        "",
        "*Fireflies Call Tracking (previous week):*",
    ]

    for r in scorecard_rows:
        icon = ":white_check_mark:" if r["ff_status"] == "OK" else ":warning:"
        lines.append(f"{icon} {r['name'].split()[0]} — {r['ff_count']} transcript(s) — {r['ff_status']}")

    lines += ["", "Per-rep audit DMs sent below."]
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Per-rep message
# -----------------------------------------------------------------------------

def _format_rep_message(rep: dict, data: dict, week_label: str, ff_data: dict) -> str:
    prefix     = message_prefix()
    oid        = rep["owner_id"]
    ff         = ff_data.get(oid, {"count": 0, "status": "NO DATA"})
    open_count = data["open_deals"]

    lines = [
        f"{prefix}Hi, this is the HubSpot Pipeline Hygiene Audit for *{rep['name']}* — from <@{ARI['slack_id']}>.",
        f"Week of {week_label} (Mon–Sun)",
    ]

    if IS_DEV:
        lines.append(
            f"_:warning: DEV MODE — actual rep: {rep['name']} | "
            f"owner ID: {rep['owner_id']} | "
            f"prod email: {rep['email']}_"
        )

    lines += [
        "",
        f"*Pipeline Summary: {open_count} open deals*",
        "```",
        f"{'Issue':<28} {'Count':>6}",
        "-" * 36,
        f"{'Past-due close date':<28} {len(data['past_due']):>6}",
        f"{'Stale (14d+ no activity)':<28} {len(data['stale']):>6}",
        f"{'Missing deal amount':<28} {len(data['missing_amount']):>6}",
        f"{'Missing pipeline source':<28} {len(data['missing_source']):>6}",
        f"{'Missing MRR':<28} {len(data['missing_mrr']):>6}",
        f"{'Missing deal status':<28} {len(data['missing_status']):>6}",
        f"{'Missing lead status':<28} {len(data['missing_lead_status']):>6}",
        "```",
    ]

    if data["past_due"]:
        lines.append(f"\n*TOP PAST-DUE DEALS (oldest first) — click to fix:*")
        for i, d in enumerate(data["past_due"][:MAX_PAST_DUE_SHOWN], 1):
            date_str = f"due {d['close_date_str']}" if d["close_date_str"] else "no close date set"
            lines.append(f"{i}. <{d['url']}|{d['name']}> — {date_str}")
        if len(data["past_due"]) > MAX_PAST_DUE_SHOWN:
            lines.append(f"_...and {len(data['past_due']) - MAX_PAST_DUE_SHOWN} more_")

    if data["stale"]:
        lines.append(f"\n*TOP STALE DEALS (worst first) — click to fix:*")
        for i, d in enumerate(data["stale"][:MAX_STALE_SHOWN], 1):
            activity_str = "no activity ever" if d["days_inactive"] is None else f"{d['days_inactive']}d inactive"
            lines.append(f"{i}. <{d['url']}|{d['name']}> — {activity_str}")
        if len(data["stale"]) > MAX_STALE_SHOWN:
            lines.append(f"_...and {len(data['stale']) - MAX_STALE_SHOWN} more_")

    if data["missing_lead_status"]:
        lines.append(f"\n*CONTACTS MISSING LEAD STATUS:*")
        for i, c in enumerate(data["missing_lead_status"][:MAX_CONTACTS_SHOWN], 1):
            lines.append(f"{i}. <{c['url']}|{c['name']}>")
        if len(data["missing_lead_status"]) > MAX_CONTACTS_SHOWN:
            lines.append(f"_...and {len(data['missing_lead_status']) - MAX_CONTACTS_SHOWN} more_")

    ff_icon = ":white_check_mark:" if ff["status"] == "OK" else ":warning:"
    lines += [
        "",
        f"*Fireflies:* {ff_icon} {ff['count']} transcript(s) last week",
        "",
        "*What to do:*",
        "1. Update or close-lost any deals with past-due close dates",
        "2. Log activity or close-lost stale deals with no engagement",
        "3. Fill in Deal Amount, Pipeline Source, MRR, and Deal Status on every open deal",
        "4. Freight-only deals: set MRR to $0 explicitly",
        "5. Assign Lead Status to all contacts without one",
        "",
        "_All unresolved issues carry forward every week until fixed._",
    ]

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Public send functions
# -----------------------------------------------------------------------------

def send_scorecard_to_ari(scorecard_rows: list, ff_data: dict) -> bool:
    week_label = _week_label()
    slack_ids  = resolve_slack_ids_for_scorecard()

    if IS_DEV:
        print(f"\n[Slack][DEV] Opening scorecard group DM → {slack_ids}")
    else:
        print(f"\n[Slack] Sending scorecard to Ari...")

    channel_id = _open_dm(slack_ids)
    if not channel_id:
        return False

    text = _format_scorecard(scorecard_rows, week_label)
    ok   = _post(channel_id, text)
    print(f"  Scorecard {'sent' if ok else 'FAILED'}.")
    return ok


def send_rep_messages(results: dict, ff_data: dict) -> None:
    week_label = _week_label()

    if IS_DEV:
        print(f"\n[Slack][DEV] Sending per-rep DMs → all routing to {resolve_slack_ids_for_rep({})}")
    else:
        print(f"\n[Slack] Sending per-rep group DMs...")

    for oid, data in results.items():
        rep       = data["rep"]
        slack_ids = resolve_slack_ids_for_rep(rep)

        print(f"  {rep['name']} → DM to {slack_ids}...")
        channel_id = _open_dm(slack_ids)
        if not channel_id:
            print(f"  FAILED to open DM for {rep['name']}.")
            continue

        text = _format_rep_message(rep, data, week_label, ff_data)
        ok   = _post(channel_id, text)
        print(f"  {'Sent' if ok else 'FAILED'}.")
        time.sleep(1)
