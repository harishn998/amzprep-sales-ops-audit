#!/usr/bin/env python3
# =============================================================================
# audit.py — AMZ Prep HubSpot Hygiene Audit (orchestrator)
# =============================================================================
# Execution order:
#   1. Fetch all HubSpot data
#   2. Fetch Fireflies transcripts
#   3. Run hygiene checks
#   4. Run AI deal analysis (GPT-4o) — new
#   5. Build scorecard
#   6. Send Slack notifications (scorecard + per-rep DMs)
#   7. Send email notifications
#
# AUDIT_MODE env var controls message format:
#   weekly   → full Monday audit
#   reminder → shorter Friday check-in
# =============================================================================

import sys
import traceback
from datetime import datetime, timezone

from hubspot_client   import fetch_all_hubspot_data
from fireflies_client import fetch_transcripts
from checks           import run_checks, build_scorecard
from ai_analyst       import run_ai_analysis
from slack_client     import send_scorecard_to_ari, send_rep_messages
from email_client     import send_rep_emails
from config           import IS_DEV, IS_FRIDAY, AUDIT_MODE


def main():
    mode_label = "FRIDAY CHECK-IN" if IS_FRIDAY else "WEEKLY AUDIT"
    env_label  = "DEV" if IS_DEV else "PRODUCTION"

    print("=" * 60)
    print(f"AMZ Prep Hygiene Audit — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Mode: {mode_label}  |  Env: {env_label}")
    print("=" * 60)

    # ── 1. HubSpot data ───────────────────────────────────────────────────────
    try:
        hs_data = fetch_all_hubspot_data()
    except Exception as e:
        print(f"\n[FATAL] HubSpot fetch failed: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── 2. Fireflies ──────────────────────────────────────────────────────────
    try:
        ff_data = fetch_transcripts()
    except Exception as e:
        print(f"\n[WARNING] Fireflies fetch failed: {e} — continuing without transcript data.")
        ff_data = {}

    # ── 3. Hygiene checks ─────────────────────────────────────────────────────
    print("\n[Checks] Running hygiene rules...")
    results = run_checks(hs_data)

    for oid, data in results.items():
        rep = data["rep"]
        print(
            f"  {rep['name']:<22}"
            f" open={data['open_deals']}"
            f" past_due={len(data['past_due'])}"
            f" no_contact={len(data['no_recent_contact'])}"
            f" stale={len(data['stale'])}"
            f" stuck_lead={len(data['stuck_lead_status'])}"
            f" calls_no_notes={len(data['calls_without_notes'])}"
        )

    # ── 4. AI analysis ────────────────────────────────────────────────────────
    try:
        run_ai_analysis(results)
    except Exception as e:
        print(f"\n[WARNING] AI analysis failed: {e} — continuing without AI insights.")
        traceback.print_exc()

    # ── 5. Scorecard ──────────────────────────────────────────────────────────
    scorecard_rows = build_scorecard(results, ff_data)

    # ── 6. Slack ──────────────────────────────────────────────────────────────
    try:
        send_scorecard_to_ari(scorecard_rows, ff_data)
        send_rep_messages(results, ff_data)
    except Exception as e:
        print(f"\n[WARNING] Slack notifications failed: {e}")
        traceback.print_exc()

    # ── 7. Email ──────────────────────────────────────────────────────────────
    try:
        send_rep_emails(results, ff_data)
    except Exception as e:
        print(f"\n[WARNING] Email notifications failed: {e}")
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("Audit complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
