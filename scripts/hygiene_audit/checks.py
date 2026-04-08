# =============================================================================
# checks.py — Hygiene rule engine
# Runs D1–D7 (deals) and L1–L3 (contacts) checks against fetched data.
# Returns a per-rep structured dict ready for Slack and email formatters.
# =============================================================================

from datetime import datetime, timezone
from config import OWNER_ID_TO_REP, REPS, STALE_DAYS, deal_url, contact_url


def _props(record: dict) -> dict:
    """Shorthand — HubSpot nests all values under 'properties'."""
    return record.get("properties", {})


def _days_since(timestamp_ms_str: str | None) -> int | None:
    """
    Convert HubSpot millisecond timestamp string to days-since-today.
    Returns None if the value is missing or unparseable.
    """
    if not timestamp_ms_str:
        return None
    try:
        ts_ms = int(timestamp_ms_str)
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - dt
        return delta.days
    except (ValueError, OSError):
        return None


def _parse_close_date(closedate_str: str | None) -> datetime | None:
    """
    HubSpot stores closedate as a date string 'YYYY-MM-DD'
    or as a millisecond timestamp string. Handle both.
    """
    if not closedate_str:
        return None
    try:
        # Try ISO date first (most common in CRM)
        return datetime.strptime(closedate_str[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        pass
    try:
        ts_ms = int(closedate_str)
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    except (ValueError, OSError):
        return None


def _is_missing(value: str | None) -> bool:
    """A field counts as missing if it's None, empty string, or literally '0'."""
    if value is None:
        return True
    stripped = str(value).strip()
    return stripped == "" or stripped == "0"


def _contact_display_name(props: dict) -> str:
    first = props.get("firstname") or ""
    last  = props.get("lastname")  or ""
    name  = f"{first} {last}".strip()
    return name if name else props.get("email", "Unknown")


# -----------------------------------------------------------------------------
# Deal checks (D1–D6)
# D7 (stuck stage) reuses notes_last_updated — combined with D2 result
# -----------------------------------------------------------------------------

def _check_deal(deal: dict, today: datetime) -> dict:
    """
    Run all deal-level checks and return a structured result dict.
    """
    p       = _props(deal)
    deal_id = deal.get("id", "")
    name    = p.get("dealname") or f"Deal {deal_id}"
    url     = deal_url(deal_id)

    # D1 — Past-due close date
    close_dt      = _parse_close_date(p.get("closedate"))
    is_past_due   = bool(close_dt and close_dt < today)
    close_date_str = close_dt.strftime("%b %d, %Y") if close_dt else None

    # D2 — Stale (no activity in 14+ days, or never)
    days_inactive  = _days_since(p.get("notes_last_updated"))
    is_stale       = days_inactive is None or days_inactive >= STALE_DAYS

    # D3–D6 — Missing fields
    amount          = p.get("amount")
    is_missing_amt  = _is_missing(amount)
    is_missing_src  = _is_missing(p.get("pipeline_source"))
    is_missing_mrr  = _is_missing(p.get("mrr"))
    is_missing_sta  = _is_missing(p.get("status_"))

    return {
        "id":              deal_id,
        "name":            name,
        "url":             url,
        "owner_id":        p.get("hubspot_owner_id", ""),
        # D1
        "is_past_due":     is_past_due,
        "close_date_str":  close_date_str,
        "close_dt":        close_dt,
        # D2
        "is_stale":        is_stale,
        "days_inactive":   days_inactive,
        # D3–D6
        "missing_amount":  is_missing_amt,
        "missing_source":  is_missing_src,
        "missing_mrr":     is_missing_mrr,
        "missing_status":  is_missing_sta,
    }


# -----------------------------------------------------------------------------
# Main aggregation
# -----------------------------------------------------------------------------

def run_checks(hs_data: dict) -> dict:
    """
    Run all hygiene checks against the HubSpot data.
    Returns a dict keyed by owner_id with per-rep audit results.

    Structure per rep:
    {
        "rep":              {rep config dict},
        "open_deals":       int,
        "past_due":         [deal result dicts, sorted oldest first],
        "stale":            [deal result dicts, sorted worst first],
        "missing_amount":   [deal result dicts],
        "missing_source":   [deal result dicts],
        "missing_mrr":      [deal result dicts],
        "missing_status":   [deal result dicts],
        "missing_lead_status":  [contact result dicts],
        "missing_lifecycle":    [contact result dicts],
        "missing_referral":     [contact result dicts],
    }
    """
    today = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Initialise per-rep buckets
    results: dict[str, dict] = {}
    for rep in REPS:
        oid = rep["owner_id"]
        results[oid] = {
            "rep":                 rep,
            "open_deals":          0,
            "past_due":            [],
            "stale":               [],
            "missing_amount":      [],
            "missing_source":      [],
            "missing_mrr":         [],
            "missing_status":      [],
            "missing_lead_status": [],
            "missing_lifecycle":   [],
            "missing_referral":    [],
        }

    # --- Deal checks ---
    for deal in hs_data["open_deals"]:
        oid = _props(deal).get("hubspot_owner_id", "")
        if oid not in results:
            continue  # Deal owned by someone outside the 6 reps — skip

        checked = _check_deal(deal, today)
        results[oid]["open_deals"] += 1

        if checked["is_past_due"]:
            results[oid]["past_due"].append(checked)
        if checked["is_stale"]:
            results[oid]["stale"].append(checked)
        if checked["missing_amount"]:
            results[oid]["missing_amount"].append(checked)
        if checked["missing_source"]:
            results[oid]["missing_source"].append(checked)
        if checked["missing_mrr"]:
            results[oid]["missing_mrr"].append(checked)
        if checked["missing_status"]:
            results[oid]["missing_status"].append(checked)

    # Sort past-due oldest first (smallest close_dt first)
    for oid in results:
        results[oid]["past_due"].sort(
            key=lambda d: d["close_dt"] or datetime.max.replace(tzinfo=timezone.utc)
        )
        # Sort stale worst first (None = never active → put at top)
        results[oid]["stale"].sort(
            key=lambda d: d["days_inactive"] if d["days_inactive"] is not None else 99999,
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
                "id":   cid,
                "name": _contact_display_name(p),
                "url":  contact_url(cid),
            })

    _add_contact("missing_lead_status", hs_data["missing_lead_status"])
    _add_contact("missing_lifecycle",   hs_data["missing_lifecycle"])
    _add_contact("missing_referral",    hs_data["missing_referral"])

    return results


def build_scorecard(results: dict, fireflies_data: dict) -> dict:
    """
    Build the consolidated scorecard row data for Ari's summary message.
    Returns a list of row dicts ordered by open_deals descending.
    """
    rows = []
    for oid, data in results.items():
        rep_ff = fireflies_data.get(oid, {"count": 0, "status": "NO DATA"})
        rows.append({
            "name":                data["rep"]["name"],
            "open_deals":          data["open_deals"],
            "past_due":            len(data["past_due"]),
            "stale":               len(data["stale"]),
            "missing_amount":      len(data["missing_amount"]),
            "missing_source":      len(data["missing_source"]),
            "missing_mrr":         len(data["missing_mrr"]),
            "missing_status":      len(data["missing_status"]),
            "missing_lead_status": len(data["missing_lead_status"]),
            "ff_count":            rep_ff["count"],
            "ff_status":           rep_ff["status"],
        })

    rows.sort(key=lambda r: r["open_deals"], reverse=True)
    return rows
