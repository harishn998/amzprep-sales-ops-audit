# =============================================================================
# config.py — AMZ Prep Hygiene Audit
# =============================================================================
#
# ENVIRONMENT SWITCH
# ------------------
# Controlled by AUDIT_ENV in hygiene-audit.yml
#
#   AUDIT_ENV=dev          → ALL notifications go to dev team only (3 people)
#   AUDIT_ENV=production   → live mode, actual AEs + stakeholders
#
# DEV MODE — who gets what:
#
#   Slack scorecard DM  → group DM: Harishnath + Jerun + Ari
#   Slack per-rep DM    → group DM: Harishnath + Jerun + Ari (labelled per rep)
#   Email To:           → harishnath@amzprep.com
#   Email CC:           → jerun@amzprep.com, ari@amzprep.com
#
#   No messages go to actual AEs (Angelo, Rich, Diggy, Chad, Nolan, Rey)
#   No messages go to Blair or Imtiaz
#   HubSpot queries still use real owner IDs — real deal/contact data
#
# To go live: change AUDIT_ENV=dev → AUDIT_ENV=production in hygiene-audit.yml
# No code changes needed.
#
# =============================================================================

import os

# -----------------------------------------------------------------------------
# Environment detection
# -----------------------------------------------------------------------------
AUDIT_ENV = os.environ.get("AUDIT_ENV", "dev").lower().strip()
IS_DEV    = AUDIT_ENV != "production"

HUBSPOT_PORTAL_ID = "878268"

# -----------------------------------------------------------------------------
# Dev team — the only 3 people who receive any notification in dev mode
# -----------------------------------------------------------------------------
DEV_HARISHNATH = {
    "name":       "Harishnath",
    "first_name": "Harishnath",
    "slack_id":   "U07HW2GFSG4",
    "email":      "harishnath@amzprep.com",
}

DEV_JERUN = {
    "name":       "Jerun Francis",
    "first_name": "Jerun",
    "slack_id":   "U0ACYKH849J",
    "email":      "jerun@amzprep.com",
}

DEV_ARI = {
    "name":       "Arishekar N",
    "first_name": "Ari",
    "slack_id":   "U06CP1PJN3Y",
    "email":      "ari@amzprep.com",
}

# All 3 dev Slack IDs — used for both scorecard and per-rep group DMs
DEV_SLACK_IDS = [
    DEV_HARISHNATH["slack_id"],   # U07HW2GFSG4
    DEV_JERUN["slack_id"],        # U0ACYKH849J
    DEV_ARI["slack_id"],          # U06CP1PJN3Y
]

# Dev email CC — all 3 dev people
DEV_EMAIL_CC = [
    DEV_HARISHNATH["email"],      # harishnath@amzprep.com
    DEV_JERUN["email"],           # jerun@amzprep.com
    DEV_ARI["email"],             # ari@amzprep.com
]

# Primary dev email recipient (To: field on per-rep emails)
DEV_EMAIL_TO = DEV_HARISHNATH["email"]

# -----------------------------------------------------------------------------
# Production reps
# HubSpot queries use these owner IDs in BOTH modes — real data always
# In dev mode, Slack/email routing is overridden — AEs never receive anything
# -----------------------------------------------------------------------------
PRODUCTION_REPS = [
    {
        "name":       "Angelo D'Onofrio",
        "first_name": "Angelo",
        "owner_id":   "1853543968",
        "slack_id":   "U06MUUDNVRS",
        "email":      "angelo@amzprep.com",
    },
    {
        "name":       "Rich Pearl",
        "first_name": "Rich",
        "owner_id":   "79083113",
        "slack_id":   "U08V34EPC5S",
        "email":      "rich@amzprep.com",
    },
    {
        "name":       "Diggy Lalussis",
        "first_name": "Diggy",
        "owner_id":   "433240944",
        "slack_id":   "U05B6GKFYR4",
        "email":      "diggy@amzprep.com",
    },
    {
        "name":       "Chad Collins",
        "first_name": "Chad",
        "owner_id":   "86868464",
        "slack_id":   "U0A7P0SSP2Q",
        "email":      "chad@amzprep.com",
    },
    {
        "name":       "Nolan Fraser",
        "first_name": "Nolan",
        "owner_id":   "86868665",
        "slack_id":   "U0A7YAJGHC1",
        "email":      "nolan@amzprep.com",
    },
    {
        "name":       "Rey Nath",
        "first_name": "Rey",
        "owner_id":   "78130835",
        "slack_id":   "U07KJB1SYEN",
        "email":      "reynath@amzprep.com",
    },
]

# REPS always uses production list — drives HubSpot queries in both modes
REPS            = PRODUCTION_REPS
OWNER_ID_TO_REP = {r["owner_id"]: r for r in REPS}
OWNER_IDS       = [r["owner_id"] for r in REPS]

# -----------------------------------------------------------------------------
# Stakeholders — production
# -----------------------------------------------------------------------------
PRODUCTION_ARI = {
    "name":     "Arishekar N",
    "slack_id": "U06CP1PJN3Y",
    "email":    "ari@amzprep.com",
}

ARI = PRODUCTION_ARI

PRODUCTION_EMAIL_CC = [
    "ari@amzprep.com",
    "blair@amzprep.com",
    "imtiaz@eshipper.com",
]

# Active CC list — dev team only in dev mode
EMAIL_CC = DEV_EMAIL_CC if IS_DEV else PRODUCTION_EMAIL_CC

# -----------------------------------------------------------------------------
# Notification routing helpers
# Called by slack_client.py and email_client.py
# -----------------------------------------------------------------------------

def resolve_slack_ids_for_rep(rep: dict) -> list:
    """
    Return the list of Slack IDs for a rep's group DM.

    Dev mode  → all 3 dev Slack IDs (Harishnath + Jerun + Ari)
    Prod mode → [rep slack_id, Ari slack_id]
    """
    if IS_DEV:
        return DEV_SLACK_IDS          # U07HW2GFSG4, U0ACYKH849J, U06CP1PJN3Y
    return [rep["slack_id"], PRODUCTION_ARI["slack_id"]]

def resolve_slack_ids_for_scorecard() -> list:
    """
    Return the list of Slack IDs for the scorecard DM.

    Dev mode  → all 3 dev Slack IDs (so everyone sees the scorecard)
    Prod mode → Ari only
    """
    if IS_DEV:
        return DEV_SLACK_IDS
    return [PRODUCTION_ARI["slack_id"]]

def resolve_email(rep: dict) -> str:
    """
    Return the To: email address for a rep's audit email.

    Dev mode  → harishnath@amzprep.com
    Prod mode → actual rep email
    """
    return DEV_EMAIL_TO if IS_DEV else rep["email"]

def message_prefix() -> str:
    """Prepend [DEV TEST] to every subject/header in dev mode."""
    return "[DEV TEST] " if IS_DEV else ""

# -----------------------------------------------------------------------------
# Email sending
# -----------------------------------------------------------------------------
EMAIL_FROM_ADDRESS = "harishnath@amzprep.com"
EMAIL_FROM_NAME    = "AMZ Prep Hygiene Audit"

# -----------------------------------------------------------------------------
# HubSpot
# -----------------------------------------------------------------------------
HUBSPOT_BASE_URL = "https://api.hubapi.com"

DEAL_PROPERTIES = [
    "dealname",
    "dealstage",
    "pipeline",
    "closedate",
    "amount",
    "hubspot_owner_id",
    "notes_last_updated",
    "notes_last_contacted",
    "pipeline_source",
    "mrr",
    "status_",
]

CONTACT_PROPERTIES = [
    "firstname",
    "lastname",
    "email",
    "hubspot_owner_id",
    "hs_lead_status",
    "lifecyclestage",
    "lead_source___amz_prep",
    "referral_partner_name",
]

CLOSED_STAGES = ["closedwon", "closedlost"]
STALE_DAYS    = 14

# -----------------------------------------------------------------------------
# HubSpot record URL helpers
# -----------------------------------------------------------------------------
def deal_url(deal_id: str) -> str:
    return f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-3/{deal_id}"

def contact_url(contact_id: str) -> str:
    return f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-1/{contact_id}"
