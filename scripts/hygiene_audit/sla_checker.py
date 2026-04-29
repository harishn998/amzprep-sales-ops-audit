# =============================================================================
# sla_checker.py — SLA breach detection engine
# =============================================================================

import os
import time
import requests
from datetime import datetime, timedelta, timezone
import pytz

from config import (
    HUBSPOT_BASE_URL, OWNER_IDS, OWNER_ID_TO_REP,
    CONTACT_PROPERTIES, DEAL_PROPERTIES, REPS,
    LEAD_SLA_MINUTES, DEAL_SLA_WARNING_DAYS, DEAL_SLA_BREACH_DAYS,
    SLA_WORK_START, SLA_WORK_END, SLA_TIMEZONE,
    SLA_LOOKBACK_HOURS, VALID_PIPELINE_SOURCES, REFERRAL_SOURCE_VALUE,
    deal_url, contact_url,
)

# Closed stage values — both human-readable and numeric pipeline IDs
# AMZ Prep 2026 pipeline (portal 878268)
CLOSED_STAGE_VALUES = {
    "closedwon", "closedlost",
    "13390264",   # Closed Won
    "13390265",   # Closed Lost
    "1271308872", # Partner Won
}

# Max deals shown per rep in a single SLA breach notification
# Prevents alert fatigue — rest summarised as count
MAX_BREACH_DEALS_NOTIFY = 10

# Only alert on deals that crossed the threshold within this many hours
# Prevents daily re-firing on old stale deals
BREACH_DETECTION_WINDOW_HOURS = 48

ET = pytz.timezone(SLA_TIMEZONE)


# =============================================================================
# Working hours + SLA deadline
# =============================================================================

def is_business_hours(dt_utc: datetime) -> bool:
    """True if dt_utc falls within 9:30 AM–6:30 PM ET, Mon–Fri."""
    dt_et = dt_utc.astimezone(ET)
    if dt_et.weekday() >= 5:
        return False
    t = (dt_et.hour, dt_et.minute)
    return SLA_WORK_START <= t <= SLA_WORK_END


def sla_deadline(submitted_utc: datetime) -> datetime:
    """
    Return UTC datetime by which the rep must log first contact.
    In hours: submitted + 30 min.
    Outside hours: next business day 9:30 AM ET + 30 min.
    """
    if is_business_hours(submitted_utc):
        return submitted_utc + timedelta(minutes=LEAD_SLA_MINUTES)

    dt_et     = submitted_utc.astimezone(ET)
    candidate = dt_et.replace(
        hour=SLA_WORK_START[0], minute=SLA_WORK_START[1],
        second=0, microsecond=0
    )
    if dt_et >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)

    candidate_aware = ET.localize(candidate.replace(tzinfo=None))
    return candidate_aware.astimezone(timezone.utc) + timedelta(minutes=LEAD_SLA_MINUTES)


def format_deadline_et(deadline_utc: datetime) -> str:
    return deadline_utc.astimezone(ET).strftime("%a %b %d %-I:%M %p ET")


def format_submitted_et(submitted_utc: datetime) -> str:
    return submitted_utc.astimezone(ET).strftime("%a %b %d %-I:%M %p ET")


# =============================================================================
# HubSpot helpers
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


# =============================================================================
# Engagement fetch — per contact (for lead SLA check)
# =============================================================================

def get_engagements_for_contact(contact_id: str, since_ms: str) -> list:
    """Fetch emails, calls, notes for a contact created after since_ms."""
    engagements = []
    for obj_type, props in [
        ("emails", ["hs_email_direction", "hs_createdate", "hubspot_owner_id"]),
        ("calls",  ["hs_call_status",     "hs_createdate", "hubspot_owner_id"]),
        ("notes",  ["hs_body_preview",    "hs_createdate", "hubspot_owner_id"]),
    ]:
        try:
            payload = {
                "filterGroups": [{
                    "filters": [
                        {"propertyName": "associations.contact", "operator": "EQ",  "value": contact_id},
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
            print(f"    [SLA] Engagement fetch ({obj_type}) skipped: {e}")
    return engagements


# =============================================================================
# New lead fetch
# =============================================================================

def get_new_leads() -> list:
    """Contacts with lead_status=NEW created in last SLA_LOOKBACK_HOURS."""
    cutoff_ms = str(int(
        (datetime.now(tz=timezone.utc) - timedelta(hours=SLA_LOOKBACK_HOURS))
        .timestamp() * 1000
    ))
    payload = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "hubspot_owner_id", "operator": "IN",  "values": OWNER_IDS},
                {"propertyName": "hs_lead_status",   "operator": "EQ",  "value":  "NEW"},
                {"propertyName": "createdate",        "operator": "GTE", "value":  cutoff_ms},
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
    Check each new lead for 30-min SLA breach.
    Returns list of breach dicts.
    """
    now_utc  = datetime.now(tz=timezone.utc)
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

        createdate_ms = p.get("createdate")
        if not createdate_ms:
            continue
        try:
            submitted_utc = datetime.fromtimestamp(int(createdate_ms) / 1000, tz=timezone.utc)
        except (ValueError, OSError):
            continue

        deadline = sla_deadline(submitted_utc)

        # Skip if SLA clock hasn't expired yet
        if deadline > now_utc:
            continue

        # Skip if breach is too old to re-notify (> 7 days)
        if now_utc - deadline > timedelta(days=7):
            continue

        since_ms    = str(int(submitted_utc.timestamp() * 1000))
        engagements = get_engagements_for_contact(contact_id, since_ms)

        deadline_ms = deadline.timestamp() * 1000
        timely = [
            e for e in engagements
            if float((e.get("properties") or {}).get("hs_createdate") or 0) <= deadline_ms
        ]

        if timely:
            continue

        first  = p.get("firstname") or ""
        last   = p.get("lastname")  or ""
        name   = f"{first} {last}".strip() or p.get("email") or f"Contact {contact_id}"
        source = p.get("lead_source___amz_prep") or ""
        hours_overdue = int((now_utc - deadline).total_seconds() / 3600)

        breach = {
            "contact_id":         contact_id,
            "contact_name":       name,
            "contact_email":      p.get("email") or "",
            "contact_url":        contact_url(contact_id),
            "rep":                rep,
            "submitted_utc":      submitted_utc,
            "deadline_utc":       deadline,
            "hours_overdue":      hours_overdue,
            "pipeline_source":    source,
            "source_missing":     not source.strip(),
            "source_invalid":     bool(source and source not in VALID_PIPELINE_SOURCES),
            "referral_missing":   (source == REFERRAL_SOURCE_VALUE and not p.get("referral_partner_name")),
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
        time.sleep(0.2)

    print(f"  [SLA] Lead SLA check complete: {len(breaches)} breach(es) found.")
    return breaches


# =============================================================================
# Deal SLA breach check
# =============================================================================

def _days_since(ts_str) -> int | None:
    """
    Parse a HubSpot timestamp to days-since-then.

    Handles both formats HubSpot returns for deal properties:
      - ISO 8601 string: '2025-03-24T17:15:00Z' (used for notes_last_updated)
      - Unix ms string:  '1745902800000' (used for some other properties)

    Returns None only if the value is absent or completely unparseable.
    """
    if not ts_str:
        return None

    now = datetime.now(tz=timezone.utc)
    s   = str(ts_str).strip()

    # ISO 8601 format: '2025-03-24T17:15:00Z', '2025-03-24T17:15:00.123Z'
    if 'T' in s:
        try:
            clean = s.rstrip('Z').rstrip('.')
            if '.' in clean:
                clean = clean[:clean.index('.')]
            dt = datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
            return max(0, (now - dt).days)
        except (ValueError, TypeError, AttributeError):
            pass

    # Unix millisecond timestamp: '1745902800000' or '1745902800000.0'
    try:
        ts_ms = int(float(s))
        dt    = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return max(0, (now - dt).days)
    except (ValueError, OSError, OverflowError, TypeError):
        pass

    return None


def _deal_days_stale(p: dict) -> int | None:
    """
    Days since the deal had any CRM activity.
    Uses the same logic as checks.py _days_since (proven working).
    Falls back to createdate if notes_last_updated is null.
    """
    # Primary: notes_last_updated (last logged activity)
    days = _days_since(p.get("notes_last_updated"))
    if days is not None:
        return days

    # Fallback: createdate (deal age — never touched since creation)
    return _days_since(p.get("createdate"))



def _get_pipeline_source(p: dict) -> str:
    """
    Read the deal pipeline source from whichever property is populated.
    HubSpot has two deal-level pipeline source fields:
      - pipeline_sourc       : auto-synced by HubSpot (shown as Pipeline Source Sync in UI, API name truncated)
      - pipeline_source      : manual entry custom field
    Check sync field first (more reliable), fall back to manual field.
    """
    return (
        (p.get("pipeline_sourc") or "").strip()
        or (p.get("pipeline_source")      or "").strip()
    )

def check_deal_sla_breaches(open_deals: list, notify_only_new: bool = True) -> dict:
    """
    Check open deals for:
      1. Missing pipeline_source        → always flagged
      2. Stale 7–14 days               → sla_warning (weekly report)
      3. Stale 14+ days                → sla_breach (immediate alert)
         - notify_only_new=True        → only deals that NEWLY crossed threshold
                                         in last 48h (daily SLA run)
         - notify_only_new=False       → ALL stale deals (force/weekly mode)

    Safety net: skips deals whose stage is in CLOSED_STAGE_VALUES.

    Returns:
      {owner_id: {rep, missing_source, sla_warning, sla_breach, sla_breach_total}}
    """
    now_utc = datetime.now(tz=timezone.utc)
    today   = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    per_rep: dict[str, dict] = {}
    for rep in REPS:
        per_rep[rep["owner_id"]] = {
            "rep":              rep,
            "missing_source":   [],
            "sla_warning":      [],    # 7–14 days stale (weekly report)
            "sla_breach":       [],    # breach entries (capped at MAX)
            "sla_breach_total": 0,     # full count before cap
        }

    skipped_closed   = 0
    skipped_old      = 0
    debug_sample     = []   # first 5 deals for debug output

    for deal in open_deals:
        p   = deal.get("properties", {})
        oid = p.get("hubspot_owner_id", "")
        if oid not in per_rep:
            continue

        # ── Safety net: skip closed stages ───────────────────────────────────
        deal_stage = (p.get("dealstage") or "").strip()
        if deal_stage in CLOSED_STAGE_VALUES:
            skipped_closed += 1
            continue

        deal_id = deal.get("id", "")
        name    = p.get("dealname") or f"Deal {deal_id}"
        url     = deal_url(deal_id)
        source  = _get_pipeline_source(p)

        # ── Missing pipeline source ───────────────────────────────────────────
        if not source:
            per_rep[oid]["missing_source"].append({
                "id": deal_id, "name": name, "url": url,
            })

        # ── Calculate staleness ───────────────────────────────────────────────
        days_stale = _deal_days_stale(p)

        entry = {
            "id":              deal_id,
            "name":            name,
            "url":             url,
            "days_stale":      days_stale,
            "pipeline_source": source or "not set",
            "deal_stage":      deal_stage,
        }

        if len(debug_sample) < 5:
            raw_lu = p.get("notes_last_updated")
            raw_cd = p.get("createdate")
            debug_sample.append({
                "name":      name[:40],
                "stage":     deal_stage,
                "last_upd":  raw_lu,
                "createdate":raw_cd,
                "stale":     days_stale,
                "raw_lu_val": str(raw_lu)[:20] if raw_lu else "None",
            })

        # ── SLA tiers ─────────────────────────────────────────────────────────
        if days_stale is not None and days_stale >= DEAL_SLA_BREACH_DAYS:
            if notify_only_new:
                # Only include if the deal crossed the threshold RECENTLY
                # (i.e. it became stale for the first time in last 48h)
                hours_since = (days_stale - DEAL_SLA_BREACH_DAYS) * 24
                if hours_since > BREACH_DETECTION_WINDOW_HOURS:
                    skipped_old += 1
                    continue

            per_rep[oid]["sla_breach_total"] += 1
            if len(per_rep[oid]["sla_breach"]) < MAX_BREACH_DEALS_NOTIFY:
                per_rep[oid]["sla_breach"].append(entry)

        elif days_stale is not None and days_stale >= DEAL_SLA_WARNING_DAYS:
            per_rep[oid]["sla_warning"].append(entry)

    # Sort: worst first
    for oid in per_rep:
        per_rep[oid]["sla_breach"].sort(
            key=lambda d: d["days_stale"] if d["days_stale"] is not None else 99999,
            reverse=True,
        )
        per_rep[oid]["sla_warning"].sort(
            key=lambda d: d["days_stale"] if d["days_stale"] is not None else 99999,
            reverse=True,
        )

    # Debug output — always printed so we can see what's happening
    total_breach = sum(r["sla_breach_total"] for r in per_rep.values())
    total_warn   = sum(len(r["sla_warning"]) for r in per_rep.values())
    print(f"  [SLA] Stage check: {skipped_closed} closed-stage deals skipped")
    print(f"  [SLA] Window filter: {skipped_old} old-breach deals skipped (notify_only_new={notify_only_new})")
    print(f"  [SLA] Result: {total_breach} breach(es), {total_warn} warning(s)")
    if debug_sample:
        print(f"  [SLA] Sample deals (first {len(debug_sample)}):")
        for d in debug_sample:
            print(f"    stage={d['stage']!r:15} stale={str(d['stale']):6} "
                  f"raw_ts={d.get('raw_lu_val', 'N/A'):22} "
                  f"name={d['name']!r}")

    return per_rep


# =============================================================================
# Pipeline source + referral validation
# =============================================================================

def check_pipeline_source_issues(contacts: list) -> list:
    """
    Check contacts for:
      L_PS1 — pipeline source missing or invalid value
      L_PS2 — source = Referral but referral_partner_name is blank
    """
    issues = []
    for contact in contacts:
        p          = contact.get("properties", {})
        contact_id = contact.get("id", "")
        oid        = p.get("hubspot_owner_id", "")
        rep        = OWNER_ID_TO_REP.get(oid)
        if not rep:
            continue

        first  = p.get("firstname") or ""
        last   = p.get("lastname")  or ""
        name   = f"{first} {last}".strip() or p.get("email") or contact_id
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
