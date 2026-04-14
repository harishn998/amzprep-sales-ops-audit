# =============================================================================
# checks.py — Hygiene rule engine
# Change: past-due filter now requires close date >= Jan 1 2025.
# Pre-2025 stale close dates are excluded from the past-due list.
# =============================================================================

from datetime import datetime, timezone
from config import OWNER_ID_TO_REP, REPS, STALE_DAYS, deal_url, contact_url

# Only flag past-due if close date is 2025 or later
# Deals left open from 2023/2024 with old close dates are excluded
PAST_DUE_MIN_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)


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


def _check_deal(deal: dict, today: datetime) -> dict:
    p       = _props(deal)
    deal_id = deal.get("id", "")
    name    = p.get("dealname") or f"Deal {deal_id}"
    url     = deal_url(deal_id)

    # D1 — Past-due: close date is before today AND on/after Jan 1 2025
    close_dt      = _parse_close_date(p.get("closedate"))
    is_past_due   = bool(
        close_dt
        and close_dt < today
        and close_dt >= PAST_DUE_MIN_DATE
    )
    close_date_str = close_dt.strftime("%b %d, %Y") if close_dt else None

    # D2 — Stale: no activity in 14+ days or never
    days_inactive = _days_since(p.get("notes_last_updated"))
    is_stale      = days_inactive is None or days_inactive >= STALE_DAYS

    return {
        "id":             deal_id,
        "name":           name,
        "url":            url,
        "owner_id":       p.get("hubspot_owner_id", ""),
        "is_past_due":    is_past_due,
        "close_date_str": close_date_str,
        "close_dt":       close_dt,
        "is_stale":       is_stale,
        "days_inactive":  days_inactive,
        "missing_amount": _is_missing(p.get("amount")),
        "missing_source": _is_missing(p.get("pipeline_source")),
        "missing_mrr":    _is_missing(p.get("mrr")),
        "missing_status": _is_missing(p.get("status_")),
    }


def run_checks(hs_data: dict) -> dict:
    today = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

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

    for deal in hs_data["open_deals"]:
        oid = _props(deal).get("hubspot_owner_id", "")
        if oid not in results:
            continue

        checked = _check_deal(deal, today)
        results[oid]["open_deals"] += 1

        if checked["is_past_due"]:    results[oid]["past_due"].append(checked)
        if checked["is_stale"]:       results[oid]["stale"].append(checked)
        if checked["missing_amount"]: results[oid]["missing_amount"].append(checked)
        if checked["missing_source"]: results[oid]["missing_source"].append(checked)
        if checked["missing_mrr"]:    results[oid]["missing_mrr"].append(checked)
        if checked["missing_status"]: results[oid]["missing_status"].append(checked)

    for oid in results:
        # Sort past-due: oldest close date first
        results[oid]["past_due"].sort(
            key=lambda d: d["close_dt"] or datetime.max.replace(tzinfo=timezone.utc)
        )
        # Sort stale: longest inactive first (None = never active = worst)
        results[oid]["stale"].sort(
            key=lambda d: d["days_inactive"] if d["days_inactive"] is not None else 99999,
            reverse=True,
        )

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


def build_scorecard(results: dict, fireflies_data: dict) -> list:
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
