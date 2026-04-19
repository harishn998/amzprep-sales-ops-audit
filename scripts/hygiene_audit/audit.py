#!/usr/bin/env python3
# =============================================================================
# audit.py — AMZ Prep HubSpot Hygiene Audit (Monday weekly orchestrator)
# =============================================================================
# Execution order:
#   1. Fetch all HubSpot data
#   2. Fetch Fireflies transcripts
#   3. Run hygiene checks (D1–D7, L1–L4, E1–E2, F1)
#   4. Run pipeline source validation (L_PS1, L_PS2)  — NEW
#   5. Build deal SLA summary for weekly report         — NEW
#   6. Run AI deal analysis (GPT-4o)
#   7. Build scorecard
#   8. Send Slack notifications (scorecard + per-rep DMs with SLA section)
#   9. Send email notifications (with SLA section)
# =============================================================================

import sys
import traceback
from datetime import datetime, timezone

from hubspot_client  import fetch_all_hubspot_data
from fireflies_client import fetch_transcripts
from checks          import (
    run_checks, build_scorecard,
    check_pipeline_source_per_rep, build_deal_sla_summary,
)
from ai_analyst      import run_ai_analysis
from slack_client    import send_scorecard_to_ari, send_rep_messages
from email_client    import send_rep_emails
from config          import IS_DEV, IS_FRIDAY, REPS


def main():
    mode_label = "FRIDAY CHECK-IN" if IS_FRIDAY else "WEEKLY AUDIT"
    env_label  = "DEV" if IS_DEV else "PRODUCTION"

    print("=" * 60)
    print(f"Kiro — AMZ Prep Hygiene Audit — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
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
        print(f"\n[WARNING] Fireflies fetch failed: {e} — continuing.")
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
        )

    # ── 4. Pipeline source validation (L_PS1, L_PS2) ─────────────────────────
    print("\n[Checks] Validating pipeline source values...")
    try:
        all_contacts = (
            hs_data["missing_lead_status"]
            + hs_data["missing_lifecycle"]
            + hs_data.get("stuck_lead_status", [])
        )
        # Deduplicate by contact ID
        seen = set()
        unique_contacts = []
        for c in all_contacts:
            if c.get("id") not in seen:
                seen.add(c.get("id"))
                unique_contacts.append(c)

        pipeline_source_issues = check_pipeline_source_per_rep(unique_contacts)
        for oid, issues in pipeline_source_issues.items():
            rep = results[oid]["rep"]
            results[oid]["pipeline_source_issues"] = issues
            if issues:
                print(f"  {rep['name']}: {len(issues)} pipeline source issue(s)")
    except Exception as e:
        print(f"  [WARNING] Pipeline source check failed: {e}")
        for oid in results:
            results[oid].setdefault("pipeline_source_issues", [])

    # ── 5. Deal SLA summary (for weekly report) ───────────────────────────────
    print("\n[Checks] Building deal SLA summary...")
    try:
        deal_sla = build_deal_sla_summary(hs_data["open_deals"])
        for oid, sla_data in deal_sla.items():
            rep = results[oid]["rep"]
            results[oid]["deal_sla_warnings"] = sla_data["warnings"]
            results[oid]["deal_sla_breaches"] = sla_data["breaches"]
            if sla_data["breaches"] or sla_data["warnings"]:
                print(
                    f"  {rep['name']}: "
                    f"SLA breaches={len(sla_data['breaches'])} "
                    f"warnings={len(sla_data['warnings'])}"
                )
    except Exception as e:
        print(f"  [WARNING] Deal SLA summary failed: {e}")
        for oid in results:
            results[oid].setdefault("deal_sla_warnings", [])
            results[oid].setdefault("deal_sla_breaches", [])

    # ── 6. AI analysis ────────────────────────────────────────────────────────
    try:
        run_ai_analysis(results)
    except Exception as e:
        print(f"\n[WARNING] AI analysis failed: {e}")
        traceback.print_exc()

    # ── 7. Scorecard ──────────────────────────────────────────────────────────
    scorecard_rows = build_scorecard(results, ff_data)

    # ── 8. Slack ──────────────────────────────────────────────────────────────
    try:
        send_scorecard_to_ari(scorecard_rows, ff_data)
        send_rep_messages(results, ff_data)
    except Exception as e:
        print(f"\n[WARNING] Slack failed: {e}")
        traceback.print_exc()

    # ── 9. Email ──────────────────────────────────────────────────────────────
    try:
        send_rep_emails(results, ff_data)
    except Exception as e:
        print(f"\n[WARNING] Email failed: {e}")
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("Audit complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
