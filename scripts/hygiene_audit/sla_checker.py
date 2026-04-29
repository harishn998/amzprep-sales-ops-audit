# =============================================================================
# sla_checker.py — SLA breach detection engine
# =============================================================================
# Handles:
#   Lead SLA  — rep must respond within 30 mins of lead submission
#               (working hours aware: 9:30 AM–6:30 PM ET, Mon–Fri)
#   Deal SLA  — open deal with no activity for 7d (warning) or 14d (breach)
#               AND pipeline source missing
#
# Called from:
#   sla_audit.py  → daily 8 AM ET cron (immediate breach alerts)
#   audit.py      → Monday weekly audit (breach summary in report)
# =============================================================================

import os
import time
import requests
from datetime import datetime, timedelta, timezone
import pytz

from config import (
    HUBSPOT_BASE_URL, OWNER_IDS, OWNER_ID_TO_REP,
    CONTACT_PROPERTIES, DEAL_PROPERTIES, CLOSED_STAGES,
    LEAD_SLA_MINUTES, DEAL_SLA_WARNING_DAYS, DEAL_SLA_BREACH_DAYS,
    SLA_WORK_START, SLA_WORK_END, SLA_TIMEZONE,
    SLA_LOOKBACK_HOURS, VALID_PIPELINE_SOURCES, REFERRAL_SOURCE_VALUE,
    REPS, deal_url, contact_url,
)

ET = pytz.timezone(SLA_TIMEZONE)


# =============================================================================
# Working hours + SLA deadline logic
# =============================================================================

def is_business_hours(dt_utc: datetime) -> bool:
    """True if dt_utc falls within working hours 9:30 AM–6:30 PM ET, Mon–Fri."""
    dt_et = dt_utc.astimezone(ET)
    if dt_et.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    t = (dt_et.hour, dt_et.minute)
    return SLA_WORK_START <= t <= SLA_WORK_END


def sla_deadline(submitted_utc: datetime) -> datetime:
    """
    Return the UTC datetime by which the rep must log first contact.

    If submitted IN working hours:
        deadline = submitted_utc + 30 minutes

    If submitted OUTSIDE working hours (after-hours, weekend, before 9:30 AM):
        deadline = next business day 9:30 AM ET + 30 minutes (= 10:00 AM ET)
    """
    if is_business_hours(submitted_utc):
        return submitted_utc + timedelta(minutes=LEAD_SLA_MINUTES)

    dt_et = submitted_utc.astimezone(ET)

    # Candidate = 9:30 AM on same day
    candidate = dt_et.replace(
        hour=SLA_WORK_START[0], minute=SLA_WORK_START[1],
        second=0, microsecond=0
    )

    # If submitted at or after 9:30 AM (meaning after business hours that day), move to tomorrow
    if dt_et >= candidate:
        candidate += timedelta(days=1)

    # Skip weekends
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)

    # Localize correctly (handles DST)
    candidate_aware = ET.localize(candidate.replace(tzinfo=None))
    return candidate_aware.astimezone(timezone.utc) + timedelta(minutes=LEAD_SLA_MINUTES)


def format_deadline_et(deadline_utc: datetime) -> str:
    return deadline_utc.astimezone(ET).strftime("%a %b %d %-I:%M %p ET")


def format_submitted_et(submitted_utc: datetime) -> str:
    return submitted_utc.astimezone(ET).strftime("%a %b %d %-I:%M %p ET")


# =============================================================================
# HubSpot engagement fetch — per contact
# =============================================================================

def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['HUBSPOT_TOKEN']}",
        "Content-Type":  "application/json",
    }


def _search(object_type: str, payload: dict) -> list:
    url     = f"{HUBSPOT_BASE_URL}/crm/v3/objects/{object_type}/search"
    records = []
    after   = None
    while True:
        if after:
            payload["after"] = after
        resp = requests.post(url, json=payload, headers=_get_headers(), timeout=30)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 10)))
            continue
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(0.15)
    return records


def get_engagements_for_contact(contact_id: str, since_ms: str) -> list:
    """
    Fetch emails, calls, and notes associated with contact_id
    that were created after since_ms (millisecond timestamp string).
    Returns a flat list of engagement dicts from all three types.
    """
    engagements = []

    for obj_type, props in [
        ("emails", ["hs_email_direction", "hs_createdate", "hubspot_owner_id"]),
        ("calls",  ["hs_call_status", "hs_createdate", "hubspot_owner_id", "hs_body_preview"]),
        ("notes",  ["hs_body_preview", "hs_createdate", "hubspot_owner_id"]),
    ]:
        try:
            payload = {
                "filterGroups": [{
                    "filters": [
                        {"propertyName": "associations.contact", "operator": "EQ", "value": contact_id},
                        {"propertyName": "hs_createdate",        "operator": "GTE", "value": since_ms},
                    ]
                }],
                "properties": props,
                "limit": 10,
            }
            results = _search(obj_type, payload)
            for r in results:
                r["_type"] = obj_type
            engagements.extend(results)
        except Exception as e:
            # Some HubSpot tiers don't expose all engagement types — skip gracefully
            print(f"    [SLA] Engagement fetch ({obj_type}) skipped: {e}")

    return engagements


# =============================================================================
# New lead fetch — contacts created in last SLA_LOOKBACK_HOURS
# =============================================================================

def get_new_leads() -> list:
    """
    Fetch contacts with lead_status = NEW created in the last 48 hours.
    These are the leads whose SLA clock may have started.
    """
    cutoff_ms = str(int(
        (datetime.now(tz=timezone.utc) - timedelta(hours=SLA_LOOKBACK_HOURS))
        .timestamp() * 1000
    ))
    payload = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "hubspot_owner_id", "operator": "IN", "values": OWNER_IDS},
                {"propertyName": "hs_lead_status",   "operator": "EQ", "value": "NEW"},
                {"propertyName": "createdate",        "operator": "GTE", "value": cutoff_ms},
            ]
        }],
        "properties": CONTACT_PROPERTIES + ["createdate"],
        "limit": 100,
    }
    try:
        contacts = _search("contacts", payload)
        print(f"  [SLA] Found {len(contacts)} new leads in last {SLA_LOOKBACK_HOURS}h.")
        return contacts
    except Exception as e:
        print(f"  [SLA] New leads fetch failed: {e}")
        return []


# =============================================================================
# Lead SLA breach check
# =============================================================================

def check_lead_sla_breaches() -> list:
    """
    For each new lead, determine if the 30-min SLA has been breached.
    Returns a list of breach dicts for notification.

    A breach occurs when:
      - SLA deadline has passed
      - No email, call, or note was logged before the deadline
    """
    now_utc = datetime.now(tz=timezone.utc)
    contacts = get_new_leads()
    breaches = []

    print(f"  [SLA] Checking {len(contacts)} new leads for SLA...")

    for contact in contacts:
        p          = contact.get("properties", {})
        contact_id = contact.get("id", "")
        oid        = p.get("hubspot_owner_id", "")
        rep        = OWNER_ID_TO_REP.get(oid)
        if not rep:
            continue

        # Parse submission time from createdate (millisecond string)
        createdate_ms = p.get("createdate")
        if not createdate_ms:
            continue
        try:
            submitted_utc = datetime.fromtimestamp(
                int(createdate_ms) / 1000, tz=timezone.utc
            )
        except (ValueError, OSError):
            continue

        deadline = sla_deadline(submitted_utc)

        # Skip if deadline hasn't passed yet — no breach possible yet
        if deadline > now_utc:
            continue

        # Skip if breach window is too old (> 7 days) — avoid re-notifying old records
        if now_utc - deadline > timedelta(days=7):
            continue

        # Check for any engagement between submission and deadline
        since_ms = str(int(submitted_utc.timestamp() * 1000))
        engagements = get_engagements_for_contact(contact_id, since_ms)

        # Filter to engagements before the deadline
        deadline_ms = deadline.timestamp() * 1000
        timely = [
            e for e in engagements
            if float((e.get("properties") or {}).get("hs_createdate") or 0) <= deadline_ms
        ]

        if timely:
            # SLA met — response was logged in time
            continue

        # SLA BREACHED
        first  = p.get("firstname") or ""
        last   = p.get("lastname")  or ""
        name   = f"{first} {last}".strip() or p.get("email") or f"Contact {contact_id}"
        email  = p.get("email") or ""
        source = p.get("lead_source___amz_prep") or "unknown"
        hours_overdue = int((now_utc - deadline).total_seconds() / 3600)

        # Pipeline source validation (check here too for context)
        source_missing  = not source or source == "unknown"
        source_invalid  = source not in VALID_PIPELINE_SOURCES and not source_missing
        referral_missing = (
            source == REFERRAL_SOURCE_VALUE
            and not p.get("referral_partner_name")
        )

        breach = {
            "contact_id":         contact_id,
            "contact_name":       name,
            "contact_email":      email,
            "contact_url":        contact_url(contact_id),
            "rep":                rep,
            "submitted_utc":      submitted_utc,
            "deadline_utc":       deadline,
            "hours_overdue":      hours_overdue,
            "pipeline_source":    source,
            "source_missing":     source_missing,
            "source_invalid":     source_invalid,
            "referral_missing":   referral_missing,
            "in_business_hours":  is_business_hours(submitted_utc),
            "submitted_str":      format_submitted_et(submitted_utc),
            "deadline_str":       format_deadline_et(deadline),
        }
        breaches.append(breach)
        print(
            f"    [SLA BREACH] {rep['name']} — {name} — "
            f"submitted {breach['submitted_str']} — "
            f"deadline was {breach['deadline_str']} — "
            f"{hours_overdue}h overdue"
        )
        time.sleep(0.2)   # Throttle engagement API calls

    print(f"  [SLA] Lead SLA check complete: {len(breaches)} breach(es) found.")
    return breaches


# =============================================================================
# Deal SLA check — pipeline source + stale severity
# =============================================================================

# Max deals to show in a single SLA breach notification per rep
# Prevents alert fatigue — rest summarised as count in the message
MAX_BREACH_DEALS_NOTIFY = 10

# Only alert on deals that became stale within this window (hours)
# Prevents re-firing on the same old deals every single day
BREACH_DETECTION_WINDOW_HOURS = 48


def check_deal_sla_breaches(open_deals: list, notify_only_new: bool = True) -> dict:
    """
    Check all open deals for:
      1. Missing pipeline_source  (always flagged)
      2. Stale 7–14 days          (warning tier — weekly report only)
      3. Stale 14+ days           (SLA breach tier)
         - notify_only_new=True  → only alert on deals that crossed threshold
                                   in the last 48h (prevents daily re-firing)
         - notify_only_new=False → return all breaches (for weekly report)

    Safety net: skips deals whose stage is in CLOSED_STAGE_IDS_STRICT,
    catching any closed deals that the API filter may have missed.
    """
    now_utc = datetime.now(tz=timezone.utc)
    today   = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    # Import strict closed stage set for in-memory double-check
    from config import CLOSED_STAGE_IDS_STRICT, DEAL_SLA_WARNING_DAYS, DEAL_SLA_BREACH_DAYS

    per_rep: dict[str, dict] = {}
    for rep in REPS:
        per_rep[rep["owner_id"]] = {
            "rep":             rep,
            "missing_source":  [],
            "sla_warning":     [],   # 7–14 days stale (weekly report)
            "sla_breach":      [],   # 14+ days stale (notify immediately)
            "sla_breach_total": 0,   # full count before capping
        }

    for deal in open_deals:
        p   = deal.get("properties", {})
        oid = p.get("hubspot_owner_id", "")
        if oid not in per_rep:
            continue

        # ── Safety net: skip deals that are actually closed ──────────────────
        # The API filter uses NOT_IN on stage names + IDs, but double-check
        # here in case the query missed any closed stage variation.
        deal_stage = (p.get("dealstage") or "").strip()
        if deal_stage in CLOSED_STAGE_IDS_STRICT:
            continue   # skip — deal is closed, not a hygiene issue

        deal_id  = deal.get("id", "")
        name     = p.get("dealname") or f"Deal {deal_id}"
        url      = deal_url(deal_id)
        source   = (p.get("pipeline_source") or "").strip()
        last_upd = p.get("notes_last_updated")

        # ── D4: Missing pipeline source ───────────────────────────────────────
        if not source:
            per_rep[oid]["missing_source"].append({
                "id": deal_id, "name": name, "url": url,
            })

        # ── Calculate days stale ─────────────────────────────────────────────
        if last_upd:
            try:
                last_dt    = datetime.fromtimestamp(int(last_upd) / 1000, tz=timezone.utc)
                last_day   = last_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                days_stale = (today - last_day).days
            except (ValueError, OSError):
                days_stale = None
        else:
            # notes_last_updated is null → deal has never been updated.
            # We use createdate to determine when it was created and how long
            # it has been sitting untouched.
            createdate = p.get("createdate")
            if createdate:
                try:
                    create_dt  = datetime.fromtimestamp(int(createdate) / 1000, tz=timezone.utc)
                    create_day = create_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    days_stale = (today - create_day).days
                except (ValueError, OSError):
                    days_stale = None
            else:
                days_stale = None

        entry = {
            "id":              deal_id,
            "name":            name,
            "url":             url,
            "days_stale":      days_stale,
            "pipeline_source": source or "not set",
            "deal_stage":      deal_stage,
            "last_updated":    last_upd,
        }

        # ── SLA breach detection ──────────────────────────────────────────────
        if days_stale is not None and days_stale >= DEAL_SLA_BREACH_DAYS:

            if notify_only_new:
                # Only include if the deal crossed the threshold RECENTLY.
                # "Recently" = it became stale exactly DEAL_SLA_BREACH_DAYS days ago
                # (i.e. within the last BREACH_DETECTION_WINDOW_HOURS hours).
                # This prevents re-firing on the same stale deal every single day.
                hours_since_threshold = (days_stale - DEAL_SLA_BREACH_DAYS) * 24
                is_new_breach = hours_since_threshold <= BREACH_DETECTION_WINDOW_HOURS
                if not is_new_breach:
                    continue   # already stale before our window — skip

            per_rep[oid]["sla_breach_total"] += 1
            # Only store up to MAX_BREACH_DEALS_NOTIFY entries for notification
            if len(per_rep[oid]["sla_breach"]) < MAX_BREACH_DEALS_NOTIFY:
                per_rep[oid]["sla_breach"].append(entry)

        elif days_stale is not None and days_stale >= DEAL_SLA_WARNING_DAYS:
            per_rep[oid]["sla_warning"].append(entry)

    # Sort: worst (longest stale) first
    for oid in per_rep:
        per_rep[oid]["sla_breach"].sort(
            key=lambda d: d["days_stale"] if d["days_stale"] is not None else 99999,
            reverse=True,
        )
        per_rep[oid]["sla_warning"].sort(
            key=lambda d: d["days_stale"] if d["days_stale"] is not None else 99999,
            reverse=True,
        )

    return per_rep


# =============================================================================
# Pipeline source + referral validation (per contact batch)
# =============================================================================

def check_pipeline_source_issues(contacts: list) -> list:
    """
    Check a list of contacts for:
      L_PS1 — pipeline source missing or not a valid dropdown value
      L_PS2 — source = Referral but referral_partner_name is blank

    Returns a flat list of issue dicts.
    """
    issues = []
    for contact in contacts:
        p          = contact.get("properties", {})
        contact_id = contact.get("id", "")
        oid        = p.get("hubspot_owner_id", "")
        rep        = OWNER_ID_TO_REP.get(oid)
        if not rep:
            continue

        first = p.get("firstname") or ""
        last  = p.get("lastname")  or ""
        name  = f"{first} {last}".strip() or p.get("email") or contact_id
        source = p.get("lead_source___amz_prep") or ""

        if not source.strip():
            issues.append({
                "contact_id":   contact_id,
                "name":         name,
                "url":          contact_url(contact_id),
                "rep":          rep,
                "issue":        "missing",
                "issue_label":  "Pipeline source not set",
                "source":       "",
            })
        elif source not in VALID_PIPELINE_SOURCES:
            issues.append({
                "contact_id":   contact_id,
                "name":         name,
                "url":          contact_url(contact_id),
                "rep":          rep,
                "issue":        "invalid",
                "issue_label":  f"Invalid source value: '{source}'",
                "source":       source,
            })
        elif source == REFERRAL_SOURCE_VALUE and not p.get("referral_partner_name"):
            issues.append({
                "contact_id":   contact_id,
                "name":         name,
                "url":          contact_url(contact_id),
                "rep":          rep,
                "issue":        "referral_missing",
                "issue_label":  "Source = Referral but Referral Partner Name is blank",
                "source":       source,
            })

    return issues
