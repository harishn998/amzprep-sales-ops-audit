# =============================================================================
# fireflies_client.py — Fireflies transcript fetch
# Fix: Fireflies GraphQL API expects Unix timestamp integers, not ISO strings
# =============================================================================

import os
import requests
from datetime import datetime, timedelta, timezone
from config import REPS, OWNER_IDS

FIREFLIES_URL = "https://api.fireflies.ai/graphql"

EMAIL_TO_OWNER_ID = {r["email"]: r["owner_id"] for r in REPS}


def _get_previous_week_range() -> tuple[int, int]:
    """
    Returns (from_ts, to_ts) as Unix timestamp integers in milliseconds.
    Fireflies API requires integer timestamps, not ISO date strings.
    Covers previous Monday 00:00 UTC → previous Sunday 23:59:59 UTC.
    """
    today             = datetime.now(tz=timezone.utc).date()
    days_since_monday = today.weekday()
    this_monday       = today - timedelta(days=days_since_monday)
    last_monday       = this_monday - timedelta(days=7)
    last_sunday       = this_monday - timedelta(days=1)

    from_dt = datetime(last_monday.year, last_monday.month, last_monday.day,
                       0, 0, 0, tzinfo=timezone.utc)
    to_dt   = datetime(last_sunday.year, last_sunday.month, last_sunday.day,
                       23, 59, 59, tzinfo=timezone.utc)

    return int(from_dt.timestamp() * 1000), int(to_dt.timestamp() * 1000)


def fetch_transcripts() -> dict:
    api_key = os.environ.get("FIREFLIES_API_KEY", "")
    if not api_key:
        print("  [Fireflies] No API key — skipping.")
        return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

    from_ts, to_ts = _get_previous_week_range()

    from_dt_str = datetime.fromtimestamp(from_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
    to_dt_str   = datetime.fromtimestamp(to_ts   / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
    print(f"\n[Fireflies] Fetching transcripts {from_dt_str} → {to_dt_str}...")

    # Fireflies GraphQL — fromDate/toDate must be Unix ms integers
    query = """
    query Transcripts($fromDate: Long, $toDate: Long) {
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
            json={
                "query":     query,
                "variables": {"fromDate": from_ts, "toDate": to_ts},
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            timeout=30,
        )

        # Print response for debugging if not 200
        if resp.status_code != 200:
            print(f"  [Fireflies] HTTP {resp.status_code}: {resp.text[:300]}")
            return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

        resp.raise_for_status()
        data = resp.json()

        # Check for GraphQL errors
        if "errors" in data:
            print(f"  [Fireflies] GraphQL errors: {data['errors']}")
            return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

    except Exception as e:
        print(f"  [Fireflies] Error: {e}")
        return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

    transcripts = data.get("data", {}).get("transcripts", []) or []
    print(f"  Found {len(transcripts)} transcripts total.")

    counts: dict[str, int] = {oid: 0 for oid in OWNER_IDS}

    for t in transcripts:
        matched = set()

        org_email = (t.get("organizer_email") or "").lower()
        if org_email in EMAIL_TO_OWNER_ID:
            matched.add(EMAIL_TO_OWNER_ID[org_email])

        for p in t.get("participants") or []:
            p_email = (p.get("email") or "").lower()
            if p_email in EMAIL_TO_OWNER_ID:
                matched.add(EMAIL_TO_OWNER_ID[p_email])

        for oid in matched:
            counts[oid] += 1

    result = {}
    for oid in OWNER_IDS:
        count  = counts[oid]
        result[oid] = {
            "count":  count,
            "status": "OK" if count > 0 else "NO CALLS RECORDED",
        }
        rep_name = next(r["name"] for r in REPS if r["owner_id"] == oid)
        print(f"  {rep_name}: {count} transcript(s) → {result[oid]['status']}")

    return result
