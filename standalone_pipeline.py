#!/usr/bin/env python3
"""
standalone_pipeline.py

Runs the Agent Intent Verifier pipeline entirely outside n8n:

    transaction request
        -> look up authorized profile (local CSV)
        -> Intent Match Agent (Claude)
        -> Behavioral Consistency Agent (Claude)
        -> Anomaly / Fraud Signal Agent (Claude)
        -> Decision Agent (Claude)
        -> append result to local audit log CSV

This reproduces the same 4 Claude calls and decision logic as the n8n
workflow (workflows/agent-intent-verifier-v5-COMPLETE.json), so it can
run standalone for local testing, demos, or environments without n8n.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python standalone_pipeline.py --agent-id agent-047 --card-member "J. Alvarez" \\
        --merchant "Amazon" --amount 42.50 --category "office_supplies"

    # Or replay every row in data/test_scenarios.csv:
    python standalone_pipeline.py --scenarios-file data/test_scenarios.csv

Requires:
    pip install anthropic
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-4-6"
PROFILES_FILE = "data/authorized_intent_profiles.csv"
AUDIT_LOG_FILE = "data/audit_log_local.csv"

AUDIT_LOG_FIELDS = [
    "timestamp", "agent_id", "card_member", "merchant", "amount", "category",
    "intent_match_score", "behavioral_score", "fraud_risk_score",
    "decision", "composite_confidence", "reasoning",
]


def load_profile(agent_id: str, card_member: str) -> dict:
    """Look up the authorized intent profile for this agent/card member from the local CSV."""
    path = Path(PROFILES_FILE)
    if not path.exists():
        print(f"Warning: {PROFILES_FILE} not found — proceeding with an empty profile.", file=sys.stderr)
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("agent_id") == agent_id and row.get("card_member") == card_member:
                return row
    print(f"Warning: no authorized profile found for agent_id={agent_id}, card_member={card_member}.", file=sys.stderr)
    return {}


def extract_json(text: str) -> dict:
    """Claude is asked to return only JSON, but this defensively extracts the {...} block just in case."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in response: {text!r}")
    return json.loads(text[start:end + 1])


def call_claude(client: anthropic.Anthropic, system: str, user_content: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    text = response.content[0].text
    return extract_json(text)


def run_intent_match_agent(client, txn: dict, profile: dict) -> dict:
    system = (
        "You are the Intent Match Agent inside an Agent Intent Verifier system for a card payments network. "
        "Your only job is to check whether an AI agent's proposed transaction fits the card member's "
        "pre-authorized intent profile. Return ONLY valid JSON, no other text, in exactly this shape: "
        '{"intent_match_score": <integer 0-100>, "amount_within_cap": <true|false>, '
        '"merchant_allowed": <true|false>, "category_authorized": <true|false>, '
        '"reasoning": "<one sentence, under 25 words>"}'
    )
    user_content = (
        f"TRANSACTION_REQUEST:\n"
        f"agent_id: {txn['agent_id']}\n"
        f"card_member: {txn['card_member']}\n"
        f"merchant: {txn['merchant']}\n"
        f"amount: {txn['amount']}\n"
        f"category: {txn['category']}\n\n"
        f"AUTHORIZED_PROFILE:\n"
        f"category: {profile.get('category', '')}\n"
        f"spend_cap: {profile.get('spend_cap', '')}\n"
        f"max_frequency: {profile.get('max_frequency', '')}\n"
        f"allowed_merchants: {profile.get('allowed_merchants', '')}\n\n"
        f"Evaluate this transaction request against this profile and return the JSON."
    )
    return call_claude(client, system, user_content)


def run_behavioral_agent(client, txn: dict) -> dict:
    system = (
        "You are the Behavioral Consistency Agent for a card payments network. Check whether a new "
        "transaction is consistent with this agent's past request pattern for this card member. If "
        "history has fewer than 3 entries, score in the low-to-mid range regardless of other factors. "
        'Return ONLY valid JSON, no other text: {"behavioral_score": <integer 0-100>, '
        '"merchant_seen_before": <true|false>, "pattern_established": <true|false>, '
        '"reasoning": "<one sentence, under 25 words>"}'
    )
    user_content = (
        f"NEW_REQUEST:\n"
        f"agent_id: {txn['agent_id']}\n"
        f"card_member: {txn['card_member']}\n"
        f"merchant: {txn['merchant']}\n"
        f"amount: {txn['amount']}\n"
        f"category: {txn['category']}\n\n"
        f"Evaluate this new request's consistency and return the JSON. Note: no historical data source "
        f"is wired yet, so base this only on internal consistency of the request itself "
        f"(reasonable amount for category, etc)."
    )
    return call_claude(client, system, user_content)


def run_fraud_agent(client, txn: dict) -> dict:
    system = (
        "You are the Anomaly/Fraud Signal Agent for a card payments network. Flag structural red flags "
        "in a transaction request. If the agent is unregistered/unknown for this card member, that alone "
        "should score in the 0-29 range regardless of other factors. Return ONLY valid JSON, no other "
        'text: {"fraud_risk_score": <integer 0-100, where 100 means no risk detected>, '
        '"agent_registered": <true|false>, "reasoning": "<one sentence, under 25 words>"}'
    )
    user_content = (
        f"TRANSACTION_REQUEST:\n"
        f"agent_id: {txn['agent_id']}\n"
        f"card_member: {txn['card_member']}\n"
        f"merchant: {txn['merchant']}\n"
        f"amount: {txn['amount']}\n"
        f"category: {txn['category']}\n\n"
        f"Known registered agents: agent-047 is registered for J. Alvarez, agent-012 is registered for "
        f"M. Chen, agent-033 is registered for R. Okafor. Any other agent_id is NOT registered. "
        f"Evaluate this request for fraud/anomaly signals and return the JSON."
    )
    return call_claude(client, system, user_content)


def run_decision_agent(client, intent_match_score: int, behavioral_score: int, fraud_risk_score: int) -> dict:
    system = (
        "You are the Decision Agent for a card payments network. Given three scores (intent match, "
        "behavioral consistency, fraud risk), decide the outcome. Decision logic: APPROVE if all three "
        "scores are 80 or above. DECLINE if the fraud risk score is below 30 (this overrides everything "
        "else), OR if all three scores are below 40. STEP_UP for everything else. Return ONLY valid "
        'JSON, no other text: {"decision": "<APPROVE|STEP_UP|DECLINE>", "composite_confidence": '
        '<integer 0-100>, "reasoning": "<two sentences max, plain language>"}'
    )
    user_content = (
        f"INTENT_MATCH_SCORE: {intent_match_score}\n"
        f"BEHAVIORAL_SCORE: {behavioral_score}\n"
        f"FRAUD_RISK_SCORE: {fraud_risk_score}\n\n"
        f"Given these three scores, decide APPROVE, STEP_UP, or DECLINE per your rules and return the JSON decision."
    )
    return call_claude(client, system, user_content)


def append_audit_log(row: dict) -> None:
    path = Path(AUDIT_LOG_FILE)
    file_exists = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_LOG_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def run_pipeline(client: anthropic.Anthropic, txn: dict) -> dict:
    profile = load_profile(txn["agent_id"], txn["card_member"])

    intent_result = run_intent_match_agent(client, txn, profile)
    behavioral_result = run_behavioral_agent(client, txn)
    fraud_result = run_fraud_agent(client, txn)

    decision_result = run_decision_agent(
        client,
        intent_result["intent_match_score"],
        behavioral_result["behavioral_score"],
        fraud_result["fraud_risk_score"],
    )

    audit_row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": txn["agent_id"],
        "card_member": txn["card_member"],
        "merchant": txn["merchant"],
        "amount": txn["amount"],
        "category": txn["category"],
        "intent_match_score": intent_result["intent_match_score"],
        "behavioral_score": behavioral_result["behavioral_score"],
        "fraud_risk_score": fraud_result["fraud_risk_score"],
        "decision": decision_result["decision"],
        "composite_confidence": decision_result["composite_confidence"],
        "reasoning": decision_result["reasoning"],
    }
    append_audit_log(audit_row)
    return audit_row


def print_result(txn: dict, result: dict) -> None:
    print(f"\n{txn['agent_id']} / {txn['card_member']} -> {txn['merchant']} (${txn['amount']}, {txn['category']})")
    print(f"  intent_match_score:    {result['intent_match_score']}")
    print(f"  behavioral_score:      {result['behavioral_score']}")
    print(f"  fraud_risk_score:      {result['fraud_risk_score']}")
    print(f"  DECISION:              {result['decision']} (confidence: {result['composite_confidence']})")
    print(f"  reasoning:             {result['reasoning']}")


def main():
    parser = argparse.ArgumentParser(description="Run the Agent Intent Verifier pipeline standalone, without n8n.")
    parser.add_argument("--agent-id")
    parser.add_argument("--card-member")
    parser.add_argument("--merchant")
    parser.add_argument("--amount")
    parser.add_argument("--category")
    parser.add_argument("--scenarios-file", help="Run every row from a CSV of test scenarios instead of a single transaction.")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: set the ANTHROPIC_API_KEY environment variable before running this script.", file=sys.stderr)
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    if args.scenarios_file:
        with open(args.scenarios_file, newline="", encoding="utf-8") as f:
            scenarios = list(csv.DictReader(f))
        if not scenarios:
            print(f"No rows found in {args.scenarios_file}.", file=sys.stderr)
            sys.exit(1)
        for txn in scenarios:
            result = run_pipeline(client, txn)
            print_result(txn, result)
    else:
        required = ["agent_id", "card_member", "merchant", "amount", "category"]
        txn = {
            "agent_id": args.agent_id,
            "card_member": args.card_member,
            "merchant": args.merchant,
            "amount": args.amount,
            "category": args.category,
        }
        missing = [k for k in required if not txn[k]]
        if missing:
            print(f"Error: missing required arguments: {', '.join('--' + m.replace('_', '-') for m in missing)}", file=sys.stderr)
            print("Or use --scenarios-file to run a batch from CSV instead.", file=sys.stderr)
            sys.exit(1)
        result = run_pipeline(client, txn)
        print_result(txn, result)

    print(f"\nAudit log written to {AUDIT_LOG_FILE}")


if __name__ == "__main__":
    main()
