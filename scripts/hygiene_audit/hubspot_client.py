# =============================================================================
# hubspot_client.py — HubSpot CRM Search API wrapper
# Handles pagination automatically. All callers get a flat list of records.
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
)


def _get_headers() -> dict:
    token = os.environ["HUBSPOT_TOKEN"]
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _search(object_type: str, payload: dict) -> list:
    """
    Paginate through HubSpot CRM Search API results.
    Returns a flat list of all matching records across all pages.
    """
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/{object_type}/search"
    records = []
    after = None

    while True:
        if after:
            payload["after"] = after

        resp = requests.post(url, json=payload, headers=_get_headers(), timeout=30)

        if resp.status_code == 429:
            # Rate limited — wait and retry
            retry_after = int(resp.headers.get("Retry-After", 10))
            print(f"  Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            continue

        resp.raise_for_status()
        data = resp.json()

        records.extend(data.get("results", []))

        paging = data.get("paging", {})
        next_page = paging.get("next", {})
        after = next_page.get("after")

        if not after:
            break

        # Be respectful of HubSpot rate limits (100 req/10s for search)
        time.sleep(0.15)

    return records


# -----------------------------------------------------------------------------
# Public query functions
# -----------------------------------------------------------------------------

def get_open_deals() -> list:
    """
    Q1: All open deals owned by AMZ Prep reps.
    Excludes closedwon and closedlost stages.
    """
    print("  Fetching open deals...")
    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "hubspot_owner_id",
                        "operator":     "IN",
                        "values":       OWNER_IDS,
                    },
                    {
                        "propertyName": "dealstage",
                        "operator":     "NOT_IN",
                        "values":       CLOSED_STAGES,
                    },
                ]
            }
        ],
        "properties": DEAL_PROPERTIES,
        "limit": 200,
    }
    deals = _search("deals", payload)
    print(f"  Found {len(deals)} open deals.")
    return deals


def get_contacts_missing_lead_status() -> list:
    """
    Q2: Contacts owned by reps with no hs_lead_status set.
    """
    print("  Fetching contacts missing lead status...")
    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "hubspot_owner_id",
                        "operator":     "IN",
                        "values":       OWNER_IDS,
                    },
                    {
                        "propertyName": "hs_lead_status",
                        "operator":     "NOT_HAS_PROPERTY",
                    },
                ]
            }
        ],
        "properties": CONTACT_PROPERTIES,
        "limit": 200,
    }
    contacts = _search("contacts", payload)
    print(f"  Found {len(contacts)} contacts missing lead status.")
    return contacts


def get_contacts_missing_lifecycle() -> list:
    """
    Q3: Contacts owned by reps with no lifecyclestage set.
    """
    print("  Fetching contacts missing lifecycle stage...")
    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "hubspot_owner_id",
                        "operator":     "IN",
                        "values":       OWNER_IDS,
                    },
                    {
                        "propertyName": "lifecyclestage",
                        "operator":     "NOT_HAS_PROPERTY",
                    },
                ]
            }
        ],
        "properties": CONTACT_PROPERTIES,
        "limit": 200,
    }
    contacts = _search("contacts", payload)
    print(f"  Found {len(contacts)} contacts missing lifecycle stage.")
    return contacts


def get_agency_contacts_missing_referral() -> list:
    """
    Q4: Contacts where pipeline source = Agency/Partner
    but referral_partner_name is not set.
    """
    print("  Fetching Agency/Partner contacts missing referral partner...")
    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "hubspot_owner_id",
                        "operator":     "IN",
                        "values":       OWNER_IDS,
                    },
                    {
                        "propertyName": "lead_source___amz_prep",
                        "operator":     "EQ",
                        "value":        "Agency/Partner",
                    },
                    {
                        "propertyName": "referral_partner_name",
                        "operator":     "NOT_HAS_PROPERTY",
                    },
                ]
            }
        ],
        "properties": CONTACT_PROPERTIES,
        "limit": 200,
    }
    contacts = _search("contacts", payload)
    print(f"  Found {len(contacts)} Agency/Partner contacts missing referral.")
    return contacts


def fetch_all_hubspot_data() -> dict:
    """
    Master fetch. Call this once from audit.py.
    Returns a dict with all four datasets.
    """
    print("\n[HubSpot] Fetching all data...")
    return {
        "open_deals":                  get_open_deals(),
        "missing_lead_status":         get_contacts_missing_lead_status(),
        "missing_lifecycle":           get_contacts_missing_lifecycle(),
        "missing_referral":            get_agency_contacts_missing_referral(),
    }
