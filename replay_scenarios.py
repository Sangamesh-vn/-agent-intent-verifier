#!/usr/bin/env python3
"""
replay_scenarios.py

Replays transaction requests from data/test_scenarios.csv against the
Agent Intent Verifier n8n webhook, and prints each pipeline decision.

Usage:
    python replay_scenarios.py --webhook-url https://your-n8n-instance/webhook/agent-intent-verifier
    python replay_scenarios.py --webhook-url <url> --file data/test_scenarios.csv
    python replay_scenarios.py --webhook-url <url> --row 3   # replay a single row (1-indexed)

Requires:
    pip install requests
"""

import argparse
import csv
import json
import sys
import time

import requests


def load_scenarios(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def post_scenario(webhook_url: str, scenario: dict, timeout: int = 30) -> dict:
    """Send one scenario to the webhook and return the parsed JSON response."""
    response = requests.post(webhook_url, json=scenario, timeout=timeout)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"raw_response": response.text}


def print_result(index: int, scenario: dict, result: dict) -> None:
    print(f"\n--- Scenario {index} ---")
    print(f"  agent_id:   {scenario.get('agent_id')}")
    print(f"  card_member:{scenario.get('card_member')}")
    print(f"  merchant:   {scenario.get('merchant')}")
    print(f"  amount:     {scenario.get('amount')}")
    print(f"  category:   {scenario.get('category')}")
    print(f"  --> response:")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Replay test scenarios against the Agent Intent Verifier webhook.")
    parser.add_argument("--webhook-url", required=True, help="Full n8n webhook URL for the pipeline entry point")
    parser.add_argument("--file", default="data/test_scenarios.csv", help="Path to the scenarios CSV (default: data/test_scenarios.csv)")
    parser.add_argument("--row", type=int, default=None, help="Replay only this row number (1-indexed). Omit to replay all rows.")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between requests when replaying multiple rows (default: 1.0)")
    args = parser.parse_args()

    try:
        scenarios = load_scenarios(args.file)
    except FileNotFoundError:
        print(f"Could not find {args.file} — run this script from the repo root, or pass --file with the correct path.", file=sys.stderr)
        sys.exit(1)

    if not scenarios:
        print(f"No rows found in {args.file}.", file=sys.stderr)
        sys.exit(1)

    if args.row is not None:
        if args.row < 1 or args.row > len(scenarios):
            print(f"--row {args.row} is out of range (file has {len(scenarios)} rows).", file=sys.stderr)
            sys.exit(1)
        targets = [(args.row, scenarios[args.row - 1])]
    else:
        targets = list(enumerate(scenarios, start=1))

    for i, scenario in targets:
        try:
            result = post_scenario(args.webhook_url, scenario)
            print_result(i, scenario, result)
        except requests.exceptions.RequestException as e:
            print(f"\n--- Scenario {i} ---")
            print(f"  Request failed: {e}", file=sys.stderr)

        if len(targets) > 1 and i != targets[-1][0]:
            time.sleep(args.delay)


if __name__ == "__main__":
    main()
