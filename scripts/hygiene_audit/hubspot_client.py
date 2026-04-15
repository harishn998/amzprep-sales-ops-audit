# =============================================================================
# hubspot_client.py — HubSpot CRM Search API wrapper
# New in this version:
#   - get_stuck_lead_status_contacts()  → L4 check
#   - get_calls_without_notes()         → E2 check
#   - hs_analytics_source added to deal fetch → D8 (deal from email thread)
# =============================================================================

import os
import time
import requests
from config import (
    HUBSPOT_BASE_URL,
    OWNER_IDS,
    DEAL_PROPERTIES,
    CONTACT_PROPERTIES,
    CLOSED_STAGES,
    STUCK_LEAD_STATUSES,
)


def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['HUBSPOT_TOKEN']}",
        "Content-Type": "application/json",
    }


def _search(object_type: str, payload: dict) -> list:
    """
    Paginate through HubSpot CRM Search API.
    Returns a flat list of all matching records across all pages.
    """
    url     = f"{HUBSPOT_BASE_URL}/crm/v3/objects/{object_type}/search"
    records = []
    after   = None

    while True:
        if after:
            payload["after"] = after

        resp = requests.post(url, json=payload, headers=_get_headers(), timeout=30)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 10))
            print(f"  Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            continue

        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("results", []))

        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

        time.sleep(0.15)

    return records


# -----------------------------------------------------------------------------
# EXISTING queries (unchanged)
# -----------------------------------------------------------------------------

def get_open_deals() -> list:
    print("  Fetching open deals...")
    payload = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "hubspot_owner_id", "operator": "IN",     "values": OWNER_IDS},
                {"propertyName": "dealstage",        "operator": "NOT_IN", "values": CLOSED_STAGES},
            ]
        }],
        "properties": DEAL_PROPERTIES,
        "limit": 200,
    }
    deals = _search("deals", payload)
    print(f"  Found {len(deals)} open deals.")
    return deals


def get_contacts_missing_lead_status() -> list:
    print("  Fetching contacts missing lead status...")
    payload = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "hubspot_owner_id", "operator": "IN",               "values": OWNER_IDS},
                {"propertyName": "hs_lead_status",   "operator": "NOT_HAS_PROPERTY"},
            ]
        }],
        "properties": CONTACT_PROPERTIES,
        "limit": 200,
    }
    contacts = _search("contacts", payload)
    print(f"  Found {len(contacts)} contacts missing lead status.")
    return contacts


def get_contacts_missing_lifecycle() -> list:
    print("  Fetching contacts missing lifecycle stage...")
    payload = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "hubspot_owner_id", "operator": "IN",               "values": OWNER_IDS},
                {"propertyName": "lifecyclestage",   "operator": "NOT_HAS_PROPERTY"},
            ]
        }],
        "properties": CONTACT_PROPERTIES,
        "limit": 200,
    }
    contacts = _search("contacts", payload)
    print(f"  Found {len(contacts)} contacts missing lifecycle stage.")
    return contacts


def get_agency_contacts_missing_referral() -> list:
    print("  Fetching Agency/Partner contacts missing referral partner...")
    payload = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "hubspot_owner_id",     "operator": "IN",               "values": OWNER_IDS},
                {"propertyName": "lead_source___amz_prep","operator": "EQ",               "value":  "Agency/Partner"},
                {"propertyName": "referral_partner_name", "operator": "NOT_HAS_PROPERTY"},
            ]
        }],
        "properties": CONTACT_PROPERTIES,
        "limit": 200,
    }
    contacts = _search("contacts", payload)
    print(f"  Found {len(contacts)} Agency/Partner contacts missing referral.")
    return contacts


# -----------------------------------------------------------------------------
# NEW query 1 — L4: contacts stuck in open lead statuses
# Flags contacts where hs_lead_status is in the "stuck open" list.
# The checks engine computes days-stuck using notes_last_updated.
# -----------------------------------------------------------------------------

def get_stuck_lead_status_contacts() -> list:
    """
    L4 check: contacts owned by reps where lead status is one of the
    "open/attempted" statuses — e.g. ATTEMPTED_TO_CONTACT, IN_PROGRESS,
    OPEN, NEW — indicating they haven't been advanced or closed.
    The checks engine filters further by days since last update.
    """
    print("  Fetching contacts with stuck lead status...")
    payload = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "hubspot_owner_id", "operator": "IN",  "values": OWNER_IDS},
                {"propertyName": "hs_lead_status",   "operator": "IN",  "values": STUCK_LEAD_STATUSES},
            ]
        }],
        "properties": CONTACT_PROPERTIES,
        "limit": 200,
    }
    contacts = _search("contacts", payload)
    print(f"  Found {len(contacts)} contacts with stuck lead status.")
    return contacts


# -----------------------------------------------------------------------------
# NEW query 2 — E2: calls logged with no body/notes
# Fetches call engagements from the last 30 days where the body is empty.
# This means a call was logged but the AE wrote nothing about the outcome.
# -----------------------------------------------------------------------------

def get_calls_without_notes() -> list:
    """
    E2 check: call engagements associated with reps' deals where hs_body_preview
    is missing — call was logged but no notes recorded.
    Uses the engagements API via CRM object search for 'calls'.
    """
    print("  Fetching calls logged without notes...")
    from datetime import datetime, timedelta, timezone as tz
    cutoff_ms = str(int((datetime.now(tz=tz.utc) - timedelta(days=30)).timestamp() * 1000))
    payload = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "hubspot_owner_id", "operator": "IN",               "values": OWNER_IDS},
                {"propertyName": "hs_body_preview",  "operator": "NOT_HAS_PROPERTY"},
                {"propertyName": "hs_createdate",    "operator": "GTE",              "value":  cutoff_ms},
            ]
        }],
        "properties": [
            "hs_call_title",
            "hs_body_preview",
            "hs_call_status",
            "hs_call_duration",
            "hubspot_owner_id",
            "hs_createdate",
            "hs_lastmodifieddate",
        ],
        "limit": 100,
    }
    try:
        calls = _search("calls", payload)
        print(f"  Found {len(calls)} calls without notes.")
        return calls
    except Exception as e:
        # Calls endpoint may not be available on all HS tiers — degrade gracefully
        print(f"  Calls fetch skipped: {e}")
        return []


# -----------------------------------------------------------------------------
# Master fetch — called once from audit.py
# -----------------------------------------------------------------------------

def fetch_all_hubspot_data() -> dict:
    print("\n[HubSpot] Fetching all data...")
    return {
        "open_deals":               get_open_deals(),
        "missing_lead_status":      get_contacts_missing_lead_status(),
        "missing_lifecycle":        get_contacts_missing_lifecycle(),
        "missing_referral":         get_agency_contacts_missing_referral(),
        "stuck_lead_status":        get_stuck_lead_status_contacts(),   # NEW L4
        "calls_without_notes":      get_calls_without_notes(),          # NEW E2
    }
