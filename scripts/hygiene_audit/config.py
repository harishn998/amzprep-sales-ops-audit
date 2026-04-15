# =============================================================================
# config.py — AMZ Prep Hygiene Audit
# =============================================================================
#
# ENVIRONMENT SWITCH
# ------------------
#   AUDIT_ENV=dev          → all notifications route to dev team only
#   AUDIT_ENV=production   → live mode, actual AEs receive their reports
#
# AUDIT_MODE
# ----------
#   AUDIT_MODE=weekly      → Monday full audit (default)
#   AUDIT_MODE=reminder    → Friday lighter check-in on unresolved issues
#
# =============================================================================

import os

# -----------------------------------------------------------------------------
# Environment + mode detection
# -----------------------------------------------------------------------------
AUDIT_ENV  = os.environ.get("AUDIT_ENV",  "dev").lower().strip()
AUDIT_MODE = os.environ.get("AUDIT_MODE", "weekly").lower().strip()
IS_DEV     = AUDIT_ENV  != "production"
IS_FRIDAY  = AUDIT_MODE == "reminder"

HUBSPOT_PORTAL_ID = "878268"

# -----------------------------------------------------------------------------
# Dev team — all 3 people receive every notification during testing
# -----------------------------------------------------------------------------
DEV_HARISHNATH = {
    "name": "Harishnath", "first_name": "Harishnath",
    "slack_id": "U07HW2GFSG4", "email": "harishnath@amzprep.com",
}
DEV_JERUN = {
    "name": "Jerun Francis", "first_name": "Jerun",
    "slack_id": "U0ACYKH849J", "email": "jerun@amzprep.com",
}
DEV_ARI = {
    "name": "Arishekar N", "first_name": "Ari",
    "slack_id": "U06CP1PJN3Y", "email": "ari@amzprep.com",
}

DEV_SLACK_IDS = [
    DEV_HARISHNATH["slack_id"],  # U07HW2GFSG4
    DEV_JERUN["slack_id"],       # U0ACYKH849J
    DEV_ARI["slack_id"],         # U06CP1PJN3Y
]
DEV_EMAIL_CC = [
    DEV_HARISHNATH["email"],     # harishnath@amzprep.com
    DEV_JERUN["email"],          # jerun@amzprep.com
    DEV_ARI["email"],            # ari@amzprep.com
]
DEV_EMAIL_TO = DEV_HARISHNATH["email"]

# -----------------------------------------------------------------------------
# Production reps
# HubSpot queries use these owner IDs in BOTH modes — real data always.
# Slack/email routing is overridden in dev mode — AEs never receive anything.
# -----------------------------------------------------------------------------
PRODUCTION_REPS = [
    {
        "name": "Angelo D'Onofrio", "first_name": "Angelo",
        "owner_id": "1853543968", "slack_id": "U06MUUDNVRS",
        "email": "angelo@amzprep.com",
    },
    {
        "name": "Rich Pearl", "first_name": "Rich",
        "owner_id": "79083113", "slack_id": "U08V34EPC5S",
        "email": "rich@amzprep.com",
    },
    {
        "name": "Diggy Lalussis", "first_name": "Diggy",
        "owner_id": "433240944", "slack_id": "U05B6GKFYR4",
        "email": "diggy@amzprep.com",
    },
    {
        "name": "Chad Collins", "first_name": "Chad",
        "owner_id": "86868464", "slack_id": "U0A7P0SSP2Q",
        "email": "chad@amzprep.com",
    },
    {
        "name": "Nolan Fraser", "first_name": "Nolan",
        "owner_id": "86868665", "slack_id": "U0A7YAJGHC1",
        "email": "nolan@amzprep.com",
    },
    {
        "name": "Rey Nath", "first_name": "Rey",
        "owner_id": "78130835", "slack_id": "U07KJB1SYEN",
        "email": "reynath@amzprep.com",
    },
]

REPS            = PRODUCTION_REPS
OWNER_ID_TO_REP = {r["owner_id"]: r for r in REPS}
OWNER_IDS       = [r["owner_id"] for r in REPS]

# -----------------------------------------------------------------------------
# Stakeholders — production
# -----------------------------------------------------------------------------
PRODUCTION_ARI = {
    "name": "Arishekar N", "slack_id": "U06CP1PJN3Y", "email": "ari@amzprep.com",
}
ARI = PRODUCTION_ARI

PRODUCTION_EMAIL_CC = [
    "ari@amzprep.com",
    "blair@amzprep.com",
    "imtiaz@eshipper.com",
]
EMAIL_CC = DEV_EMAIL_CC if IS_DEV else PRODUCTION_EMAIL_CC

# -----------------------------------------------------------------------------
# Hygiene thresholds
# -----------------------------------------------------------------------------
STALE_DAYS             = 14   # D2 — days with no activity before flagging stale
ENGAGEMENT_STALE_DAYS  = 14   # E1 — days since last contact (call/email) before flagging
STUCK_LEAD_STATUS_DAYS = 7    # L4 — days "Attempted to Contact" before escalating
PAST_DUE_MIN_DATE_STR  = "2025-01-01"   # D1 — ignore pre-2025 close dates

# Lead statuses that are "stuck open" and need follow-up
# Contacts stuck in these statuses for 7+ days without activity are flagged (L4)
# Deliberately excludes OPEN/NEW — those are legitimate unworked states, not stuck
STUCK_LEAD_STATUSES = ["ATTEMPTED_TO_CONTACT", "IN_PROGRESS"]

# -----------------------------------------------------------------------------
# AI Analyst (OpenAI)
# -----------------------------------------------------------------------------
OPENAI_MODEL         = "gpt-4o"
AI_MAX_DEALS_PER_REP = 10    # max deals sent to AI per rep (cost control)
AI_ENABLED           = True  # set False to skip AI without changing code

# -----------------------------------------------------------------------------
# Notification routing helpers
# -----------------------------------------------------------------------------
def resolve_slack_ids_for_rep(rep: dict) -> list:
    if IS_DEV:
        return DEV_SLACK_IDS
    return [rep["slack_id"], PRODUCTION_ARI["slack_id"]]

def resolve_slack_ids_for_scorecard() -> list:
    if IS_DEV:
        return DEV_SLACK_IDS
    return [PRODUCTION_ARI["slack_id"]]

def resolve_email(rep: dict) -> str:
    return DEV_EMAIL_TO if IS_DEV else rep["email"]

def message_prefix() -> str:
    prefix = ""
    if IS_DEV:
        prefix += "[DEV TEST] "
    if IS_FRIDAY:
        prefix += "[FRIDAY CHECK-IN] "
    return prefix

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
    "hs_analytics_source",    # NEW — detect deals originating from email threads
    "createdate",             # NEW — for deal age context in AI analysis
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
    "notes_last_contacted",   # NEW — used for L4 stuck status check
    "notes_last_updated",     # NEW — used for L4 stuck status check
]

CLOSED_STAGES = ["closedwon", "closedlost"]

# -----------------------------------------------------------------------------
# HubSpot record URL helpers
# -----------------------------------------------------------------------------
def deal_url(deal_id: str) -> str:
    return f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-3/{deal_id}"

def contact_url(contact_id: str) -> str:
    return f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-1/{contact_id}"
