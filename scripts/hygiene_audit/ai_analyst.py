# =============================================================================
# ai_analyst.py — GPT-4o deal risk analysis
# =============================================================================
# For each rep, takes their top N flagged deals, sends them to OpenAI in a
# single structured prompt, and returns risk/reason/action per deal.
# Results are injected back into the deal dicts before Slack/email formatting.
#
# Graceful degradation: if OPENAI_API_KEY is missing or the API fails,
# the audit continues normally — AI fields remain None and are hidden in output.
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
    lines = [
        f"Deal: {deal['name']}",
        f"Days inactive (no CRM activity): {ctx.get('days_inactive') or 'never active'}",
        f"Days since last contact (call/email): {ctx.get('days_since_contact') or 'never contacted'}",
        f"Close date: {ctx.get('close_date') or 'not set'} {'(PAST DUE)' if ctx.get('is_past_due') else ''}",
        f"Deal amount: ${ctx.get('amount') or '0'}",
        f"MRR: ${ctx.get('mrr') or '0'}",
        f"Pipeline source: {ctx.get('pipeline_source')}",
        f"Deal status: {ctx.get('deal_status')}",
        f"Analytics source: {ctx.get('analytics_source')}",
        f"Deal age: {ctx.get('deal_age_days') or 'unknown'} days",
    ]

    # Flag which hygiene issues this deal triggered
    flags = []
    if deal.get("is_past_due"):              flags.append("past-due close date")
    if deal.get("is_stale"):                 flags.append("stale (no CRM activity)")
    if deal.get("no_recent_contact"):        flags.append("no contact logged in 14+ days")
    if deal.get("missing_amount"):           flags.append("missing deal amount")
    if deal.get("missing_source"):           flags.append("missing pipeline source")
    if deal.get("missing_mrr"):              flags.append("missing MRR")
    if deal.get("missing_status"):           flags.append("missing deal status")
    if deal.get("created_from_email_no_followup"): flags.append("came from email — no follow-up contact logged")

    if flags:
        lines.append(f"Hygiene flags: {', '.join(flags)}")

    return "\n".join(lines)


SYSTEM_PROMPT = """You are a sales operations analyst for AMZ Prep, a 3PL fulfillment company.
You receive HubSpot deal data for sales reps and assess deal health.

For each deal provided, respond with a JSON array where each element has exactly:
{
  "deal_name": "<exact deal name as given>",
  "risk": "<High | Medium | Low>",
  "reason": "<1 concise sentence explaining the core problem>",
  "action": "<1 specific, actionable next step for the rep this week>"
}

Rules:
- Risk is High if: past-due AND stale AND no contact, or no contact ever, or deal age > 90 days with no activity
- Risk is Medium if: one major issue present (stale OR past-due OR missing fields)
- Risk is Low if: minor hygiene issues only (missing fields, recently active)
- Reason must be specific to this deal's data — never generic
- Action must be concrete: who to call, what to send, whether to close-lost
- Never use placeholder phrases like "review the deal" — be directive
- Respond ONLY with the JSON array, no preamble, no markdown fences"""


def analyse_rep_deals(rep: dict, results: dict) -> None:
    """
    Analyses the top N most concerning deals for a rep using GPT-4o.
    Injects ai_risk, ai_reason, ai_action fields directly into each deal dict.
    Modifies results in-place. Returns nothing.
    """
    if not AI_ENABLED:
        return

    client = _get_client()
    if client is None:
        return

    oid = rep["owner_id"]
    data = results.get(oid, {})

    # Collect the most concerning deals — prioritised by severity
    # Past-due + stale are highest priority, then no_recent_contact, then stale only
    priority_deals = []
    seen_ids = set()

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

    # Build the prompt
    deal_blocks = "\n\n".join(
        f"--- Deal {i+1} ---\n{_build_deal_context(d)}"
        for i, d in enumerate(priority_deals)
    )

    user_prompt = f"""Analyse these {len(priority_deals)} deals for {rep['name']} at AMZ Prep.
Each deal has one or more hygiene issues flagged. Provide risk, reason, and action for each.

{deal_blocks}"""

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,        # Low temp for consistent structured output
            max_tokens=1500,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()

        # GPT-4o with json_object mode returns a top-level object — unwrap if needed
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            # May be wrapped: {"deals": [...]} or {"results": [...]} etc.
            for key in ("deals", "results", "analysis", "data"):
                if isinstance(parsed.get(key), list):
                    parsed = parsed[key]
                    break
            else:
                # Try to find the first list value
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break

        if not isinstance(parsed, list):
            print(f"  [AI] Unexpected response format for {rep['name']}")
            return

        # Map results back to deal dicts by deal name
        name_to_result = {item.get("deal_name", ""): item for item in parsed}

        for deal in priority_deals:
            match = name_to_result.get(deal["name"])
            if match:
                deal["ai_risk"]   = match.get("risk")
                deal["ai_reason"] = match.get("reason")
                deal["ai_action"] = match.get("action")

        risk_counts = {"High": 0, "Medium": 0, "Low": 0}
        for deal in priority_deals:
            if deal.get("ai_risk") in risk_counts:
                risk_counts[deal["ai_risk"]] += 1
        print(f"  {rep['name']}: {len(priority_deals)} deals analysed — "
              f"High={risk_counts['High']} Med={risk_counts['Medium']} Low={risk_counts['Low']}")

    except json.JSONDecodeError as e:
        print(f"  [AI] JSON parse error for {rep['name']}: {e}")
    except Exception as e:
        print(f"  [AI] API error for {rep['name']}: {e}")


def run_ai_analysis(results: dict) -> None:
    """
    Entry point from audit.py.
    Runs AI analysis for all reps sequentially with a small delay
    to avoid OpenAI rate limits.
    """
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
        time.sleep(0.5)   # Respect OpenAI rate limits between reps
