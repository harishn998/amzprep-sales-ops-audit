#!/usr/bin/env python3
# =============================================================================
# sla_audit.py — Daily SLA breach check (entry point)
# =============================================================================
# Runs every weekday at 8:00 AM ET (13:00 UTC) via GitHub Actions.
# Lighter than audit.py — no Fireflies, no AI, no full scorecard.
#
# What it checks:
#   1. New leads (lead_status=NEW, last 48h) → 30-min SLA response check
#   2. Open deals → missing pipeline source + newly stale (14d+) SLA breach
#   3. Fires immediate Slack DM + email for every new breach found
#
# FORCE MODE (testing only):
#   Set env var  AUDIT_SLA_FORCE=1  to bypass the 48h new-breach window.
#   This shows ALL stale deals, not just newly breached ones.
#   Use this to verify notification format — revert to 0 after testing.
# =============================================================================

import os
import sys
import traceback
from datetime import datetime, timezone

from sla_checker  import check_lead_sla_breaches, check_deal_sla_breaches
from sla_notifier import notify_lead_sla_breach, notify_deal_sla_breaches
from hubspot_client import get_open_deals
from config import IS_DEV, REPS


def main():
    env_label  = "DEV" if IS_DEV else "PRODUCTION"
    force_all  = os.environ.get("AUDIT_SLA_FORCE", "").strip() == "1"

    print("=" * 60)
    print(f"Kiro — Daily SLA Check — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Env: {env_label}")
    if force_all:
        print("Mode: FORCE — showing ALL stale deals (48h window bypassed)")
    print("=" * 60)

    total_lead_breaches = 0
    total_deal_breaches = 0

    # ── 1. Lead SLA check ────────────────────────────────────────────────────
    print("\n[Lead SLA] Checking new leads for 30-min response breaches...")
    try:
        lead_breaches = check_lead_sla_breaches()
        for breach in lead_breaches:
            try:
                notify_lead_sla_breach(breach)
                total_lead_breaches += 1
            except Exception as e:
                print(f"  [ERROR] Notify lead breach failed: {e}")
                traceback.print_exc()
    except Exception as e:
        print(f"  [ERROR] Lead SLA check failed: {e}")
        traceback.print_exc()

    # ── 2. Deal SLA check ────────────────────────────────────────────────────
    if force_all:
        print("\n[Deal SLA] FORCE MODE — checking ALL stale deals (no 48h filter)...")
    else:
        print("\n[Deal SLA] Checking open deals for new stale SLA breaches (48h window)...")

    try:
        open_deals  = get_open_deals()
        # notify_only_new=False in force mode → show all stale deals
        # notify_only_new=True  in normal mode → only deals that newly crossed 14d threshold
        deal_result = check_deal_sla_breaches(open_deals, notify_only_new=not force_all)

        for oid, data in deal_result.items():
            rep         = data["rep"]
            breaches    = data["sla_breach"]        # capped to MAX_BREACH_DEALS_NOTIFY
            total_count = data["sla_breach_total"]  # full count before cap

            if breaches:
                print(
                    f"  {rep['name']}: {len(breaches)} breach(es) shown "
                    f"(of {total_count} total stale) — notifying..."
                )
                try:
                    notify_deal_sla_breaches(rep, breaches, total_count=total_count)
                    total_deal_breaches += len(breaches)
                except Exception as e:
                    print(f"  [ERROR] Notify deal breach failed for {rep['name']}: {e}")
                    traceback.print_exc()
            else:
                warnings = len(data["sla_warning"])
                missing  = len(data["missing_source"])
                print(
                    f"  {rep['name']}: no {'(force)' if force_all else 'new'} breaches"
                    f" | total_stale={total_count}"
                    f" | warnings={warnings}"
                    f" | missing_source={missing}"
                )

    except Exception as e:
        print(f"  [ERROR] Deal SLA check failed: {e}")
        traceback.print_exc()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Daily SLA check complete.")
    print(f"  Lead SLA breaches notified: {total_lead_breaches}")
    print(f"  Deal SLA breaches notified: {total_deal_breaches}")
    if force_all:
        print("  [FORCE MODE WAS ON — set AUDIT_SLA_FORCE=0 after testing]")
    print("=" * 60)


if __name__ == "__main__":
    main()
