# =============================================================================
# fireflies_client.py — Fireflies transcript fetch
# Fix: use String type with ISO date, fetch last 50 and filter in Python
# Fireflies does not support custom scalar types like Long in their schema
# =============================================================================

import os
import requests
from datetime import datetime, timedelta, timezone
from config import REPS, OWNER_IDS

FIREFLIES_URL = "https://api.fireflies.ai/graphql"

EMAIL_TO_OWNER_ID = {r["email"]: r["owner_id"] for r in REPS}


def _get_previous_week_bounds() -> tuple[datetime, datetime, str, str]:
    """
    Returns (from_dt, to_dt, from_str, to_str) for the previous Mon–Sun.
    Dates used both for display and for Python-side filtering of results.
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

    return from_dt, to_dt, last_monday.isoformat(), last_sunday.isoformat()


def fetch_transcripts() -> dict:
    """
    Fetch Fireflies transcripts for the previous week.
    Strategy: fetch the 50 most recent transcripts (no date filter in GraphQL
    since Fireflies schema varies), then filter by date in Python.
    Returns dict keyed by owner_id: {"count": int, "status": str}
    """
    api_key = os.environ.get("FIREFLIES_API_KEY", "")
    if not api_key:
        print("  [Fireflies] No API key — skipping.")
        return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

    from_dt, to_dt, from_str, to_str = _get_previous_week_bounds()
    print(f"\n[Fireflies] Fetching transcripts {from_str} → {to_str}...")

    # Simple query without date variables — avoids schema type issues
    # Fetch last 50 transcripts and filter by date in Python
    query = """
    {
      transcripts(limit: 50) {
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
            json={"query": query},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"  [Fireflies] HTTP {resp.status_code} — {resp.text[:400]}")
            return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

        data = resp.json()

        if "errors" in data:
            print(f"  [Fireflies] GraphQL errors: {data['errors']}")
            return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

    except Exception as e:
        print(f"  [Fireflies] Request failed: {e}")
        return {oid: {"count": 0, "status": "NO DATA"} for oid in OWNER_IDS}

    all_transcripts = data.get("data", {}).get("transcripts", []) or []
    print(f"  Fetched {len(all_transcripts)} recent transcripts — filtering to {from_str} → {to_str}...")

    # Filter to previous week's window in Python
    # Fireflies 'date' field is a Unix timestamp in milliseconds
    transcripts = []
    for t in all_transcripts:
        raw_date = t.get("date")
        if raw_date is None:
            continue
        try:
            ts_ms   = int(raw_date)
            t_dt    = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            if from_dt <= t_dt <= to_dt:
                transcripts.append(t)
        except (ValueError, OSError):
            continue

    print(f"  {len(transcripts)} transcript(s) in the target week.")

    # Count per rep by matching organizer_email or participant emails
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
