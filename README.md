# Agent Intent Verifier

An AI-powered intent verification workflow for autonomous agent payments — built to answer the question: *"Was this AI agent actually authorized to make this transaction?"*

As AI agents start making purchases on behalf of humans (via protocols like Agent Purchase Protection), payment networks need a way to verify that an agent's proposed transaction actually fits what its human principal authorized — not just that the agent has valid credentials. This project is a working prototype of that verification layer, including a human-in-the-loop **Step-Up approval** path for transactions that can't be auto-approved or auto-declined with confidence.

## How it works

```
Lovable form (submit transaction request)
        │
        ▼
n8n Webhook ──▶ Airtable (lookup authorized intent profile)
        │
        ▼
┌─────────────────────────────────────────────┐
│           4-Agent Verification Pipeline       │
│                                                │
│  1. Intent Match Agent                        │
│     — does this fit the pre-authorized        │
│       category, spend cap, merchant list?     │
│                                                │
│  2. Behavioral Consistency Agent               │
│     — is this consistent with the agent's     │
│       past request pattern?                   │
│                                                │
│  3. Anomaly / Fraud Signal Agent               │
│     — is the agent even registered for this   │
│       card member? any structural red flags?  │
│                                                │
│  4. Decision Agent                             │
│     — combines all 3 scores into a final       │
│       APPROVE / STEP_UP / DECLINE              │
└─────────────────────────────────────────────┘
        │
        ▼
Airtable audit log (every request + score + decision recorded)
        │
        ▼
Step-Up approval screen (human reviews and approves/denies
STEP_UP transactions before they proceed)
```

Each agent is a Claude API call with a narrow, single-responsibility system prompt — the pipeline is intentionally structured as separate agents rather than one large prompt, so each decision is independently auditable and each score can be inspected on its own.

## Decision logic

The Decision Agent combines the three upstream scores:

| Condition | Outcome |
|---|---|
| Intent match, behavioral, and fraud scores all ≥ 80 | **APPROVE** |
| Fraud risk score < 30 (overrides everything else) | **DECLINE** |
| All three scores < 40 | **DECLINE** |
| Everything else | **STEP_UP** — routed to human review |

`STEP_UP` transactions surface in the approval screen, where a human reviews the agent's reasoning and the three underlying scores before approving or denying.

## Repo structure

```
workflows/   n8n workflow exports (the actual pipeline, importable into n8n)
docs/        per-agent system prompt documentation
data/        sample/test data — transaction history, authorized profiles, test scenarios
schema/      data schema definitions
screenshots/ screenshots of the full flow, including the Step-Up approval screen
```

| File | Description |
|---|---|
| `workflows/agent-intent-verifier-v1-webhook-airtable.json` | Webhook entry point + Airtable profile lookup |
| `workflows/agent-intent-verifier-v2-intent-match-agent.json` | Intent Match Agent |
| `workflows/agent-intent-verifier-v3-behavioral-agent.json` | + Behavioral Consistency Agent |
| `workflows/agent-intent-verifier-v4-fraud-agent.json` | + Anomaly/Fraud Signal Agent |
| `workflows/agent-intent-verifier-v5-COMPLETE.json` | Full pipeline including Decision Agent |

## Tech stack

- **[n8n](https://n8n.io)** — workflow orchestration engine running the agent pipeline
- **[Claude API](https://www.anthropic.com/api)** (`claude-sonnet-4-6`) — powers all 4 verification agents
- **[Airtable](https://airtable.com)** — stores authorized intent profiles and serves as the audit log
- **[Lovable](https://lovable.dev)** — front-end form for submitting transaction requests and the Step-Up approval screen

## Setup

1. Import the workflow JSON files into your n8n instance (start with `v5-COMPLETE.json` for the full pipeline, or import `v1`–`v4` incrementally to see the pipeline built up step by step).
2. Set up credentials in n8n:
   - **Airtable**: Personal Access Token, configured as an n8n credential (see the `airtableTokenApi` node)
   - **Anthropic**: API key, configured as a Header Auth credential (`x-api-key`) on each HTTP Request node calling the Claude API
3. Create the Airtable base with tables matching the schema in `schema/` — one table for authorized intent profiles, one for the audit log.
4. Point the Lovable form's submit action at your n8n webhook URL.
5. Test using the sample data in `data/test_scenarios.csv`.

> **Note:** The exported workflow JSON files do not contain any credentials — all API keys and tokens are referenced via n8n's credential store and must be configured in your own n8n instance.

## Status

Working end-to-end prototype: form submission → 4-agent scoring pipeline → decision → audit log → human Step-Up approval loop. Built as an exploration of what an intent-verification layer for agentic payments could look like.
