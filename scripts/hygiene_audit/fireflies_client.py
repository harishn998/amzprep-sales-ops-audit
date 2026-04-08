# =============================================================================
# fireflies_client.py — Fireflies transcript fetch
# Queries the previous Mon–Sun window and cross-references reps by email.
# =============================================================================

import os
import requests
from datetime import datetime, timedelta, timezone
from config import REPS, OWNER_IDS


FIREFLIES_URL = "https://api.fireflies.ai/graphql"

# Map rep email → owner_id for cross-referencing transcripts
EMAIL_TO_OWNER_ID = {r["email"]: r["owner_id"] for r in REPS}


def _get_previous_week_range() -> tuple[str, str]:
    """
    Returns (from_date, to_date) as ISO date strings for the previous Mon–Sun.
    E.g. if today is Monday April 7 → returns "2026-03-30", "2026-04-05"
    """
    today = datetime.now(tz=timezone.utc).date()
    # Most recent Monday (today if Monday, else go back)
    days_since_monday = today.weekday()
    this_monday = today - timedelta(days=days_since_monday)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    return last_monday.isoformat(), last_sunday.isoformat()


def fetch_transcripts() -> dict:
    """
    Fetch Fireflies transcripts for the previous week.
    Returns a dict keyed by owner_id:
    {
        owner_id: {
            "count":  int,
            "status": "OK" | "NO CALLS RECORDED" | "NO DATA",
        }
    }
    """
    api_key = os.environ.get("FIREFLIES_API_KEY", "")
    if not api_key:
        print("  [Fireflies] No API key — skipping.")
        return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

    from_date, to_date = _get_previous_week_range()
    print(f"\n[Fireflies] Fetching transcripts {from_date} → {to_date}...")

    query = """
    query Transcripts($fromDate: String, $toDate: String) {
      transcripts(fromDate: $fromDate, toDate: $toDate, limit: 100) {
        id
        title
        date
        organizer_email
        participants {
          email
        }
      }
    }
    """

    try:
        resp = requests.post(
            FIREFLIES_URL,
            json={"query": query, "variables": {"fromDate": from_date, "toDate": to_date}},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [Fireflies] Error fetching transcripts: {e}")
        return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

    transcripts = data.get("data", {}).get("transcripts", []) or []
    print(f"  Found {len(transcripts)} transcripts total.")

    # Count transcripts per rep by matching organizer_email or participant emails
    counts: dict[str, int] = {oid: 0 for oid in OWNER_IDS}

    for t in transcripts:
        matched_owners = set()

        # Check organizer
        org_email = (t.get("organizer_email") or "").lower()
        if org_email in EMAIL_TO_OWNER_ID:
            matched_owners.add(EMAIL_TO_OWNER_ID[org_email])

        # Check all participants
        for p in t.get("participants") or []:
            p_email = (p.get("email") or "").lower()
            if p_email in EMAIL_TO_OWNER_ID:
                matched_owners.add(EMAIL_TO_OWNER_ID[p_email])

        for oid in matched_owners:
            counts[oid] += 1

    # Build final result
    result = {}
    for oid in OWNER_IDS:
        count = counts[oid]
        result[oid] = {
            "count":  count,
            "status": "OK" if count > 0 else "NO CALLS RECORDED",
        }
        rep_name = next(r["name"] for r in REPS if r["owner_id"] == oid)
        print(f"  {rep_name}: {count} transcript(s) → {result[oid]['status']}")

    return result
