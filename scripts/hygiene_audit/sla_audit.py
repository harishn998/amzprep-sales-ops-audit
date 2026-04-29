#!/usr/bin/env python3
# =============================================================================
# sla_audit.py — Daily SLA breach check (entry point)
# =============================================================================
# Runs every weekday at 8:00 AM ET (13:00 UTC) via GitHub Actions.
# Much lighter than audit.py — no Fireflies, no AI, no full scorecard.
#
# What it checks:
#   1. New leads (lead_status=NEW, last 48h) → 30-min SLA response check
#   2. Open deals → missing pipeline source + 14d+ stale SLA breach
#   3. Fires immediate Slack DM + email for every breach found
#
# Monday's full audit.py also includes an SLA summary section — this daily
# job catches breaches the same day so reps get immediate feedback.
# =============================================================================

import sys
import traceback
from datetime import datetime, timezone

from sla_checker  import check_lead_sla_breaches, check_deal_sla_breaches
from sla_notifier import notify_lead_sla_breach, notify_deal_sla_breaches
from hubspot_client import get_open_deals
from config import IS_DEV, REPS


def main():
    env_label = "DEV" if IS_DEV else "PRODUCTION"
    print("=" * 60)
    print(f"Kiro — Daily SLA Check — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Env: {env_label}")
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
    print("\n[Deal SLA] Checking open deals for stale SLA breaches...")
    try:
        open_deals  = get_open_deals()
        deal_result = check_deal_sla_breaches(open_deals)

        for oid, data in deal_result.items():
            rep          = data["rep"]
            breaches     = data["sla_breach"]       # capped to MAX_BREACH_DEALS_NOTIFY
            total_count  = data["sla_breach_total"]  # real count before cap

            if breaches:
                print(f"  {rep['name']}: {len(breaches)} new breach(es) (of {total_count} total stale) — notifying...")
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
                    f"  {rep['name']}: no new breaches today"
                    f" | total_stale={total_count} | warnings={warnings} | missing_source={missing}"
                )

    except Exception as e:
        print(f"  [ERROR] Deal SLA check failed: {e}")
        traceback.print_exc()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"Daily SLA check complete.")
    print(f"  Lead SLA breaches notified: {total_lead_breaches}")
    print(f"  Deal SLA breaches notified: {total_deal_breaches}")
    print("=" * 60)


if __name__ == "__main__":
    main()
