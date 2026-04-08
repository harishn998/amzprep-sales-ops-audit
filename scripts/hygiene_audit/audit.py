#!/usr/bin/env python3
# =============================================================================
# audit.py — AMZ Prep HubSpot Hygiene Audit
# Main orchestrator. Run by GitHub Actions every Monday at 9 AM ET.
#
# Execution order:
#   1. Fetch all HubSpot data (deals + contacts)
#   2. Fetch Fireflies transcripts (previous week)
#   3. Run hygiene checks → per-rep results
#   4. Build consolidated scorecard
#   5. Send Ari's scorecard DM (Slack)
#   6. Send per-rep group DMs (Slack — rep + Ari)
#   7. Send per-rep emails (SendGrid — Ari, Blair, Imtiaz CC'd)
# =============================================================================

import sys
import traceback
from datetime import datetime, timezone

from hubspot_client  import fetch_all_hubspot_data
from fireflies_client import fetch_transcripts
from checks          import run_checks, build_scorecard
from slack_client    import send_scorecard_to_ari, send_rep_messages
from email_client    import send_rep_emails


def main():
    print("=" * 60)
    print(f"AMZ Prep Hygiene Audit — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Fetch HubSpot data
    # ------------------------------------------------------------------
    try:
        hs_data = fetch_all_hubspot_data()
    except Exception as e:
        print(f"\n[FATAL] HubSpot fetch failed: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Fetch Fireflies transcripts
    # ------------------------------------------------------------------
    try:
        ff_data = fetch_transcripts()
    except Exception as e:
        print(f"\n[WARNING] Fireflies fetch failed: {e}. Continuing without transcript data.")
        ff_data = {}

    # ------------------------------------------------------------------
    # 3. Run hygiene checks
    # ------------------------------------------------------------------
    print("\n[Checks] Running hygiene rules...")
    results = run_checks(hs_data)

    # Summary to stdout for GitHub Actions logs
    for oid, data in results.items():
        rep = data["rep"]
        print(
            f"  {rep['name']:<20} "
            f"open={data['open_deals']} "
            f"past_due={len(data['past_due'])} "
            f"stale={len(data['stale'])} "
            f"no_status={len(data['missing_status'])} "
            f"no_lead={len(data['missing_lead_status'])}"
        )

    # ------------------------------------------------------------------
    # 4. Build scorecard
    # ------------------------------------------------------------------
    scorecard_rows = build_scorecard(results, ff_data)

    # ------------------------------------------------------------------
    # 5 & 6. Slack notifications
    # ------------------------------------------------------------------
    try:
        send_scorecard_to_ari(scorecard_rows, ff_data)
        send_rep_messages(results, ff_data)
    except Exception as e:
        print(f"\n[WARNING] Slack notifications failed: {e}")
        traceback.print_exc()

    # ------------------------------------------------------------------
    # 7. Email notifications
    # ------------------------------------------------------------------
    try:
        send_rep_emails(results, ff_data)
    except Exception as e:
        print(f"\n[WARNING] Email notifications failed: {e}")
        traceback.print_exc()

    # ------------------------------------------------------------------
    # Done
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Audit complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
