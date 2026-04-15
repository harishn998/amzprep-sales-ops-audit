# =============================================================================
# ai_analyst.py — GPT-4o deal risk analysis
# =============================================================================
# For each rep, takes their top N flagged deals, sends them to OpenAI in a
# single structured prompt, and returns risk/reason/action per deal.
# Results are injected back into the deal dicts before Slack/email formatting.
#
# Graceful degradation: if OPENAI_API_KEY is missing or the API fails,
# the audit continues normally — AI fields remain None and are hidden.
# =============================================================================

import os
import json
import time
from openai import OpenAI
from config import OPENAI_MODEL, AI_MAX_DEALS_PER_REP, AI_ENABLED

_client: OpenAI | None = None


def _get_client() -> OpenAI | None:
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    _client = OpenAI(api_key=api_key)
    return _client


def _build_deal_context(deal: dict) -> str:
    """Format a single deal's context into a compact human-readable block."""
    ctx = deal.get("ai_context", {})
    flags = []
    if deal.get("is_past_due"):                        flags.append("past-due close date")
    if deal.get("is_stale"):                           flags.append("stale (no CRM activity)")
    if deal.get("no_recent_contact"):                  flags.append("no contact logged in 14+ days")
    if deal.get("missing_amount"):                     flags.append("missing deal amount")
    if deal.get("missing_source"):                     flags.append("missing pipeline source")
    if deal.get("missing_mrr"):                        flags.append("missing MRR")
    if deal.get("missing_status"):                     flags.append("missing deal status")
    if deal.get("created_from_email_no_followup"):     flags.append("email-sourced, no follow-up contact")

    lines = [
        f"Deal name: {deal['name']}",
        f"Days since CRM activity: {ctx.get('days_inactive') or 'never active'}",
        f"Days since last contact (call/email): {ctx.get('days_since_contact') or 'never contacted'}",
        f"Close date: {ctx.get('close_date') or 'not set'}{' (PAST DUE)' if ctx.get('is_past_due') else ''}",
        f"Deal amount: ${ctx.get('amount') or '0'}",
        f"MRR: ${ctx.get('mrr') or '0'}",
        f"Pipeline source: {ctx.get('pipeline_source') or 'unknown'}",
        f"Deal status field: {ctx.get('deal_status') or 'empty'}",
        f"Deal age: {ctx.get('deal_age_days') or 'unknown'} days",
        f"Hygiene flags: {', '.join(flags) if flags else 'none'}",
    ]
    return "\n".join(lines)


# The system prompt tells GPT exactly what key to use — eliminates wrapper ambiguity
SYSTEM_PROMPT = """You are a sales operations analyst for AMZ Prep, a 3PL fulfillment company.
Analyse the HubSpot deals provided and assess health for the sales rep.

Respond with a JSON object in EXACTLY this format — no other keys, no markdown, no preamble:
{"deals": [
  {"deal_name": "<exact deal name>", "risk": "<High|Medium|Low>", "reason": "<1 sentence>", "action": "<1 specific step>"},
  ...
]}

Risk rules:
- High: past-due AND never contacted, OR no contact ever, OR 90+ days inactive
- Medium: one major issue (stale OR past-due OR missing critical fields)
- Low: minor hygiene issues only (missing fields, recently active)

Reason: specific to this deal's data — never generic
Action: concrete directive — who to call, what email to send, whether to close-lost
Never say "review the deal" — be specific and directive."""


def _parse_gpt_response(raw: str, rep_name: str) -> list:
    """
    Bulletproof parser that handles all GPT-4o response formats.
    Checks for the exact key we requested first, then falls back
    to breadth-first search for any list of deal dicts.
    Logs the raw response on failure so we can debug.
    """
    try:
        # Strip any accidental markdown code fences
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()

        parsed = json.loads(cleaned)

        # Best case: GPT followed our exact format {"deals": [...]}
        if isinstance(parsed, dict) and isinstance(parsed.get("deals"), list):
            return parsed["deals"]

        # It's already a bare array
        if isinstance(parsed, list):
            return parsed

        # GPT used a different wrapper key — find any list of dicts with deal_name
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list) and v and isinstance(v[0], dict) and "deal_name" in v[0]:
                    return v
            # Recurse one level deeper (nested wrapping)
            for v in parsed.values():
                if isinstance(v, dict):
                    for vv in v.values():
                        if isinstance(vv, list) and vv and isinstance(vv[0], dict):
                            return vv

        # Nothing worked — log the raw response so we can see what GPT returned
        print(f"  [AI] Could not extract deal list for {rep_name}.")
        print(f"  [AI] Raw response (first 300 chars): {raw[:300]}")
        return []

    except json.JSONDecodeError as e:
        print(f"  [AI] JSON parse error for {rep_name}: {e}")
        print(f"  [AI] Raw response (first 300 chars): {raw[:300]}")
        return []


def analyse_rep_deals(rep: dict, results: dict) -> None:
    """
    Analyse top N most concerning deals for a rep using GPT-4o.
    Injects ai_risk, ai_reason, ai_action into each deal dict in-place.
    """
    if not AI_ENABLED:
        return

    client = _get_client()
    if client is None:
        return

    oid  = rep["owner_id"]
    data = results.get(oid, {})

    # Collect deals by priority: past_due first, then no_contact, then stale
    priority_deals = []
    seen_ids       = set()

    def _add(bucket_key: str):
        for d in data.get(bucket_key, []):
            if d["id"] not in seen_ids and len(priority_deals) < AI_MAX_DEALS_PER_REP:
                priority_deals.append(d)
                seen_ids.add(d["id"])

    _add("past_due")
    _add("no_recent_contact")
    _add("stale")
    _add("created_from_email_no_followup")

    if not priority_deals:
        return

    deal_blocks = "\n\n".join(
        f"--- Deal {i+1} ---\n{_build_deal_context(d)}"
        for i, d in enumerate(priority_deals)
    )

    user_prompt = (
        f"Analyse these {len(priority_deals)} deals for {rep['name']} at AMZ Prep.\n\n"
        f"{deal_blocks}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )

        raw    = response.choices[0].message.content or ""
        parsed = _parse_gpt_response(raw, rep["name"])

        if not parsed:
            return

        # Map results back to deal dicts by exact deal name
        name_to_result = {item.get("deal_name", ""): item for item in parsed}

        matched = 0
        for deal in priority_deals:
            result = name_to_result.get(deal["name"])
            if result:
                deal["ai_risk"]   = result.get("risk")
                deal["ai_reason"] = result.get("reason")
                deal["ai_action"] = result.get("action")
                matched += 1

        risk_counts = {"High": 0, "Medium": 0, "Low": 0}
        for deal in priority_deals:
            if deal.get("ai_risk") in risk_counts:
                risk_counts[deal["ai_risk"]] += 1

        print(
            f"  {rep['name']}: {len(priority_deals)} deals sent, {matched} matched — "
            f"High={risk_counts['High']} Med={risk_counts['Medium']} Low={risk_counts['Low']}"
        )

    except Exception as e:
        print(f"  [AI] API error for {rep['name']}: {e}")


def run_ai_analysis(results: dict) -> None:
    """Entry point from audit.py. Runs AI analysis for all reps."""
    client = _get_client()
    if client is None:
        print("\n[AI] No OPENAI_API_KEY — skipping AI analysis.")
        return

    print(f"\n[AI] Running deal analysis with {OPENAI_MODEL}...")
    for oid, data in results.items():
        rep = data["rep"]
        print(f"  Analysing {rep['name']}...")
        try:
            analyse_rep_deals(rep, results)
        except Exception as e:
            print(f"  [AI] Failed for {rep['name']}: {e}")
        time.sleep(0.5)
