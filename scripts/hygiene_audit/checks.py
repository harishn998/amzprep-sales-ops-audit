# =============================================================================
# checks.py — Hygiene rule engine
# =============================================================================
# Rules:
#   D1  — Past-due close date (2025+ only)
#   D2  — Stale deal (no activity 14d+)
#   D3  — Missing deal amount
#   D4  — Missing pipeline source
#   D5  — Missing MRR
#   D6  — Missing deal status
#   D7  — Deal from email thread but no follow-up contact logged
#   L1  — Missing lead status
#   L2  — Missing lifecycle stage
#   L3  — Agency/Partner with no referral partner name
#   L4  — Stuck lead status (Attempted/In Progress/Open/New > 7 days) — NEW
#   E1  — No contact (call/email) in 14+ days on deal              — NEW
#   E2  — Call logged with no notes                                — NEW
# =============================================================================

from datetime import datetime, timezone
from config import (
    OWNER_ID_TO_REP, REPS,
    STALE_DAYS, ENGAGEMENT_STALE_DAYS, STUCK_LEAD_STATUS_DAYS,
    PAST_DUE_MIN_DATE_STR,
    deal_url, contact_url,
)

PAST_DUE_MIN_DATE = datetime.fromisoformat(PAST_DUE_MIN_DATE_STR).replace(tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# Shared helpers
# -----------------------------------------------------------------------------

def _props(record: dict) -> dict:
    return record.get("properties", {})


def _days_since(timestamp_ms_str) -> int | None:
    if not timestamp_ms_str:
        return None
    try:
        ts_ms = int(timestamp_ms_str)
        dt    = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return (datetime.now(tz=timezone.utc) - dt).days
    except (ValueError, OSError):
        return None


def _parse_close_date(closedate_str) -> datetime | None:
    if not closedate_str:
        return None
    try:
        return datetime.strptime(closedate_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(int(closedate_str) / 1000, tz=timezone.utc)
    except (ValueError, OSError):
        return None


def _is_missing(value) -> bool:
    if value is None:
        return True
    return str(value).strip() in ("", "0")


def _contact_display_name(props: dict) -> str:
    first = props.get("firstname") or ""
    last  = props.get("lastname")  or ""
    name  = f"{first} {last}".strip()
    return name if name else props.get("email", "Unknown")


# -----------------------------------------------------------------------------
# Deal checks
# -----------------------------------------------------------------------------

def _check_deal(deal: dict, today: datetime) -> dict:
    p       = _props(deal)
    deal_id = deal.get("id", "")
    name    = p.get("dealname") or f"Deal {deal_id}"
    url     = deal_url(deal_id)

    # D1 — Past-due: close date before today AND on/after 2025-01-01
    close_dt      = _parse_close_date(p.get("closedate"))
    is_past_due   = bool(
        close_dt
        and close_dt < today
        and close_dt >= PAST_DUE_MIN_DATE
    )
    close_date_str = close_dt.strftime("%b %d, %Y") if close_dt else None

    # D2 — Stale: no HubSpot activity in 14+ days or never
    days_inactive = _days_since(p.get("notes_last_updated"))
    is_stale      = days_inactive is None or days_inactive >= STALE_DAYS

    # E1 — No contact (call/email logged) in 14+ days
    days_since_contact = _days_since(p.get("notes_last_contacted"))
    no_recent_contact  = days_since_contact is None or days_since_contact >= ENGAGEMENT_STALE_DAYS

    # D7 — Deal originated from email but has no follow-up contact logged
    analytics_source  = (p.get("hs_analytics_source") or "").upper()
    is_email_sourced  = "EMAIL" in analytics_source
    no_contact_ever   = p.get("notes_last_contacted") is None
    created_from_email_no_followup = is_email_sourced and no_contact_ever

    # Build AI context string (used later by ai_analyst.py)
    deal_age_days = _days_since(p.get("createdate"))
    ai_context = {
        "days_inactive":       days_inactive,
        "days_since_contact":  days_since_contact,
        "close_date":          close_date_str,
        "is_past_due":         is_past_due,
        "amount":              p.get("amount") or "0",
        "pipeline_source":     p.get("pipeline_source") or "unknown",
        "deal_status":         p.get("status_") or "not set",
        "analytics_source":    analytics_source or "unknown",
        "deal_age_days":       deal_age_days,
        "mrr":                 p.get("mrr") or "0",
    }

    return {
        "id":               deal_id,
        "name":             name,
        "url":              url,
        "owner_id":         p.get("hubspot_owner_id", ""),
        # D1
        "is_past_due":      is_past_due,
        "close_date_str":   close_date_str,
        "close_dt":         close_dt,
        # D2
        "is_stale":         is_stale,
        "days_inactive":    days_inactive,
        # E1
        "no_recent_contact":       no_recent_contact,
        "days_since_contact":      days_since_contact,
        # D7
        "created_from_email_no_followup": created_from_email_no_followup,
        # D3–D6
        "missing_amount":   _is_missing(p.get("amount")),
        "missing_source":   _is_missing(p.get("pipeline_source")),
        "missing_mrr":      _is_missing(p.get("mrr")),
        "missing_status":   _is_missing(p.get("status_")),
        # AI context payload
        "ai_context":       ai_context,
        # AI fields — populated later by ai_analyst.py
        "ai_risk":    None,
        "ai_reason":  None,
        "ai_action":  None,
    }


# -----------------------------------------------------------------------------
# Main aggregation
# -----------------------------------------------------------------------------

def run_checks(hs_data: dict) -> dict:
    """
    Run all hygiene rules against fetched HubSpot data.
    Returns a dict keyed by owner_id with per-rep results.
    """
    today = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    results: dict[str, dict] = {}
    for rep in REPS:
        oid = rep["owner_id"]
        results[oid] = {
            "rep":                          rep,
            "open_deals":                   0,
            # Deal buckets
            "past_due":                     [],
            "stale":                        [],
            "no_recent_contact":            [],
            "created_from_email_no_followup": [],
            "missing_amount":               [],
            "missing_source":               [],
            "missing_mrr":                  [],
            "missing_status":               [],
            # Contact buckets
            "missing_lead_status":          [],
            "missing_lifecycle":            [],
            "missing_referral":             [],
            "stuck_lead_status":            [],  # NEW L4
            # Engagement bucket
            "calls_without_notes":          [],  # NEW E2
        }

    # --- Deal checks ---
    for deal in hs_data["open_deals"]:
        oid = _props(deal).get("hubspot_owner_id", "")
        if oid not in results:
            continue

        checked = _check_deal(deal, today)
        results[oid]["open_deals"] += 1

        if checked["is_past_due"]:                       results[oid]["past_due"].append(checked)
        if checked["is_stale"]:                          results[oid]["stale"].append(checked)
        if checked["no_recent_contact"]:                 results[oid]["no_recent_contact"].append(checked)
        if checked["created_from_email_no_followup"]:    results[oid]["created_from_email_no_followup"].append(checked)
        if checked["missing_amount"]:                    results[oid]["missing_amount"].append(checked)
        if checked["missing_source"]:                    results[oid]["missing_source"].append(checked)
        if checked["missing_mrr"]:                       results[oid]["missing_mrr"].append(checked)
        if checked["missing_status"]:                    results[oid]["missing_status"].append(checked)

    # Sort buckets
    for oid in results:
        results[oid]["past_due"].sort(
            key=lambda d: d["close_dt"] or datetime.max.replace(tzinfo=timezone.utc)
        )
        results[oid]["stale"].sort(
            key=lambda d: d["days_inactive"] if d["days_inactive"] is not None else 99999,
            reverse=True,
        )
        results[oid]["no_recent_contact"].sort(
            key=lambda d: d["days_since_contact"] if d["days_since_contact"] is not None else 99999,
            reverse=True,
        )

    # --- Contact checks ---
    def _add_contact(bucket_key: str, contacts: list):
        for contact in contacts:
            p   = _props(contact)
            oid = p.get("hubspot_owner_id", "")
            if oid not in results:
                continue
            cid = contact.get("id", "")
            results[oid][bucket_key].append({
                "id":          cid,
                "name":        _contact_display_name(p),
                "url":         contact_url(cid),
                "lead_status": p.get("hs_lead_status", ""),
                "days_since_update": _days_since(p.get("notes_last_updated")),
            })

    _add_contact("missing_lead_status", hs_data["missing_lead_status"])
    _add_contact("missing_lifecycle",   hs_data["missing_lifecycle"])
    _add_contact("missing_referral",    hs_data["missing_referral"])

    # --- L4: Stuck lead status ---
    # Filter further: only flag if stuck for STUCK_LEAD_STATUS_DAYS+ days
    for contact in hs_data.get("stuck_lead_status", []):
        p   = _props(contact)
        oid = p.get("hubspot_owner_id", "")
        if oid not in results:
            continue

        days_stuck = _days_since(p.get("notes_last_updated"))
        # Only flag if truly stuck (beyond threshold), not just recently set
        if days_stuck is None or days_stuck >= STUCK_LEAD_STATUS_DAYS:
            cid = contact.get("id", "")
            results[oid]["stuck_lead_status"].append({
                "id":         cid,
                "name":       _contact_display_name(p),
                "url":        contact_url(cid),
                "lead_status": p.get("hs_lead_status", ""),
                "days_stuck": days_stuck,
            })

    # Sort stuck contacts: longest stuck first
    for oid in results:
        results[oid]["stuck_lead_status"].sort(
            key=lambda c: c["days_stuck"] if c["days_stuck"] is not None else 99999,
            reverse=True,
        )

    # --- E2: Calls without notes ---
    for call in hs_data.get("calls_without_notes", []):
        p   = _props(call)
        oid = p.get("hubspot_owner_id", "")
        if oid not in results:
            continue
        cid = call.get("id", "")
        results[oid]["calls_without_notes"].append({
            "id":    cid,
            "title": p.get("hs_call_title") or "Untitled call",
            "date":  p.get("hs_createdate", ""),
        })

    return results


def build_scorecard(results: dict, fireflies_data: dict) -> list:
    rows = []
    for oid, data in results.items():
        rep_ff = fireflies_data.get(oid, {"count": 0, "status": "NO DATA"})
        rows.append({
            "name":                   data["rep"]["name"],
            "open_deals":             data["open_deals"],
            "past_due":               len(data["past_due"]),
            "stale":                  len(data["stale"]),
            "no_recent_contact":      len(data["no_recent_contact"]),
            "missing_amount":         len(data["missing_amount"]),
            "missing_source":         len(data["missing_source"]),
            "missing_mrr":            len(data["missing_mrr"]),
            "missing_status":         len(data["missing_status"]),
            "missing_lead_status":    len(data["missing_lead_status"]),
            "stuck_lead_status":      len(data["stuck_lead_status"]),
            "calls_without_notes":    len(data["calls_without_notes"]),
            "email_no_followup":      len(data["created_from_email_no_followup"]),
            "ff_count":               rep_ff["count"],
            "ff_status":              rep_ff["status"],
        })
    rows.sort(key=lambda r: r["open_deals"], reverse=True)
    return rows


# =============================================================================
# NEW — Pipeline source validation (L_PS1, L_PS2)
# =============================================================================

from config import VALID_PIPELINE_SOURCES, REFERRAL_SOURCE_VALUE

def check_pipeline_source_per_rep(contacts: list) -> dict:
    """
    For a flat list of contacts, group pipeline source issues per rep.
    Returns {owner_id: [issue_dict, ...]}
    """
    from config import OWNER_ID_TO_REP
    per_rep: dict[str, list] = {rep["owner_id"]: [] for rep in REPS}

    for contact in contacts:
        p   = _props(contact)
        oid = p.get("hubspot_owner_id", "")
        if oid not in per_rep:
            continue

        cid    = contact.get("id", "")
        name   = _contact_display_name(p)
        source = p.get("lead_source___amz_prep") or ""

        if not source.strip():
            issue_label = "Pipeline source not set"
            issue_code  = "missing"
        elif source not in VALID_PIPELINE_SOURCES:
            issue_label = f"Invalid value: '{source}'"
            issue_code  = "invalid"
        elif source == REFERRAL_SOURCE_VALUE and not p.get("referral_partner_name"):
            issue_label = "Referral partner name missing"
            issue_code  = "referral_missing"
        else:
            continue  # No issue — skip

        per_rep[oid].append({
            "id":          cid,
            "name":        name,
            "url":         contact_url(cid),
            "issue":       issue_code,
            "issue_label": issue_label,
            "source":      source,
        })

    return per_rep


# =============================================================================
# NEW — Deal SLA severity summary (for weekly report section)
# =============================================================================

from config import DEAL_SLA_WARNING_DAYS, DEAL_SLA_BREACH_DAYS

def build_deal_sla_summary(open_deals: list) -> dict:
    """
    Returns per-rep summary of SLA warning/breach counts for the weekly report.
    {owner_id: {"rep": ..., "warnings": [...], "breaches": [...], "missing_source": [...]}}
    """
    per_rep: dict[str, dict] = {}
    for rep in REPS:
        per_rep[rep["owner_id"]] = {
            "rep":            rep,
            "missing_source": [],
            "warnings":       [],
            "breaches":       [],
        }

    today = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    for deal in open_deals:
        p   = _props(deal)
        oid = p.get("hubspot_owner_id", "")
        if oid not in per_rep:
            continue

        deal_id = deal.get("id", "")
        name    = p.get("dealname") or f"Deal {deal_id}"
        url     = deal_url(deal_id)
        source  = p.get("pipeline_source") or ""

        if not source.strip():
            per_rep[oid]["missing_source"].append({"id": deal_id, "name": name, "url": url})

        days_inactive = _days_since(p.get("notes_last_updated"))
        stale = days_inactive if days_inactive is not None else 9999

        entry = {"id": deal_id, "name": name, "url": url, "days_stale": days_inactive}
        if stale >= DEAL_SLA_BREACH_DAYS:
            per_rep[oid]["breaches"].append(entry)
        elif stale >= DEAL_SLA_WARNING_DAYS:
            per_rep[oid]["warnings"].append(entry)

    return per_rep
