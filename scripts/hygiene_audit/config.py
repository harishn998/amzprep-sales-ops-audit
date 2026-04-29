# =============================================================================
# config.py — AMZ Prep Hygiene Audit + SLA Checker
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
#   AUDIT_MODE=sla         → Daily SLA breach check (weekdays 8 AM ET)
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
IS_SLA     = AUDIT_MODE == "sla"

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
    DEV_HARISHNATH["email"],
    DEV_JERUN["email"],
    DEV_ARI["email"],
]
DEV_EMAIL_TO = DEV_HARISHNATH["email"]

# -----------------------------------------------------------------------------
# Production reps
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
EMAIL_TO_REP    = {r["email"]: r for r in REPS}

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
# Existing hygiene thresholds (unchanged)
# -----------------------------------------------------------------------------
STALE_DAYS             = 14
ENGAGEMENT_STALE_DAYS  = 14
STUCK_LEAD_STATUS_DAYS = 7
PAST_DUE_MIN_DATE_STR  = "2025-01-01"
STUCK_LEAD_STATUSES    = ["ATTEMPTED_TO_CONTACT", "IN_PROGRESS"]

# -----------------------------------------------------------------------------
# NEW — SLA thresholds
# -----------------------------------------------------------------------------
# Lead SLA: rep must respond within 30 mins of lead submission (working hours)
LEAD_SLA_MINUTES        = 30

# Deal SLA severity tiers (days with no activity on open deal)
DEAL_SLA_WARNING_DAYS   = 7    # Flag in weekly report as warning
DEAL_SLA_BREACH_DAYS    = 14   # Flag as SLA breach + immediate notification

# Working hours for SLA calculation (Eastern Time)
SLA_WORK_START          = (9, 30)   # 9:30 AM ET
SLA_WORK_END            = (18, 30)  # 6:30 PM ET
SLA_TIMEZONE            = "America/New_York"

# How far back to look for new leads in daily SLA check (hours)
SLA_LOOKBACK_HOURS      = 48   # 48h covers any missed leads + weekend gap

# Valid values for the Pipeline Source (lead_source___amz_prep) contact property.
# These must match the exact dropdown option values in HubSpot.
# If HubSpot adds/changes options, update this list to match.
VALID_PIPELINE_SOURCES = [
    "Direct",
    "Referral",
    "Agency/Partner",
    "Inbound - Website",
    "Inbound - Social",
    "Outbound - Cold Email",
    "Outbound - Cold Call",
    "Conference/Event",
    "Re-engagement",
    "Other",
]

# Pipeline source value that triggers the referral partner name requirement
REFERRAL_SOURCE_VALUE = "Referral"

# -----------------------------------------------------------------------------
# AI Analyst (OpenAI) — unchanged
# -----------------------------------------------------------------------------
OPENAI_MODEL         = "gpt-4o"
AI_MAX_DEALS_PER_REP = 10
AI_ENABLED           = True

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

def resolve_slack_ids_for_sla_breach(rep: dict) -> list:
    """SLA breach alerts: rep + Ari. Dev: dev team only."""
    if IS_DEV:
        return DEV_SLACK_IDS
    return [rep["slack_id"], PRODUCTION_ARI["slack_id"]]

def resolve_email(rep: dict) -> str:
    return DEV_EMAIL_TO if IS_DEV else rep["email"]

def resolve_sla_breach_email_cc(to_email: str) -> list:
    """CC list for immediate SLA breach emails."""
    if IS_DEV:
        return [e for e in DEV_EMAIL_CC if e.lower() != to_email.lower()]
    return [e for e in PRODUCTION_EMAIL_CC if e.lower() != to_email.lower()]

def message_prefix() -> str:
    prefix = ""
    if IS_DEV:
        prefix += "[DEV TEST] "
    if IS_FRIDAY:
        prefix += "[FRIDAY CHECK-IN] "
    if IS_SLA:
        prefix += "[SLA ALERT] "
    return prefix

# -----------------------------------------------------------------------------
# Email sending
# -----------------------------------------------------------------------------
EMAIL_FROM_ADDRESS = "harishnath@amzprep.com"
EMAIL_FROM_NAME    = "Kiro — AMZ Prep Sales Ops"

# -----------------------------------------------------------------------------
# HubSpot
# -----------------------------------------------------------------------------
HUBSPOT_BASE_URL = "https://api.hubapi.com"

DEAL_PROPERTIES = [
    "dealname", "dealstage", "pipeline", "closedate",
    "amount", "hubspot_owner_id",
    "notes_last_updated", "notes_last_contacted",
    "pipeline_source",      # manual entry field
    "pipeline_source_sync", # auto-synced from HubSpot (shown as Pipeline Source Sync)
    "mrr", "status_",
    "hs_analytics_source", "createdate",
]

CONTACT_PROPERTIES = [
    "firstname", "lastname", "email",
    "hubspot_owner_id", "hs_lead_status", "lifecyclestage",
    "lead_source___amz_prep", "referral_partner_name",
    "notes_last_contacted", "notes_last_updated",
    "createdate",   # NEW — needed for SLA deadline calculation
]

# Deal stages that are considered closed — both human-readable names AND
# numeric stage IDs used in the AMZ Prep 2026 pipeline (portal 878268).
# HubSpot stores dealstage as the stage internal value, which can be either
# a name (e.g. "closedwon") or a pipeline-specific numeric ID.
# Both forms must be listed here so the NOT_IN filter catches all closed deals.
CLOSED_STAGES = [
    # Standard HubSpot names
    "closedwon",
    "closedlost",
    # AMZ Prep 2026 pipeline numeric stage IDs
    "13390264",    # Closed Won
    "13390265",    # Closed Lost
    "1271308872",  # Partner Won (also closed — not actively pursued)
    "13390263",    # Contract Sent (edge case — keep in open for now, excluded below)
]

# Stages that are definitely closed — used for SLA breach filtering
# (stricter than CLOSED_STAGES — Partner Won is included)
CLOSED_STAGE_IDS_STRICT = {
    "13390264", "13390265", "1271308872",
    "closedwon", "closedlost",
}

# -----------------------------------------------------------------------------
# HubSpot record URL helpers
# -----------------------------------------------------------------------------
def deal_url(deal_id: str) -> str:
    return f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-3/{deal_id}"

def contact_url(contact_id: str) -> str:
    return f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-1/{contact_id}"
