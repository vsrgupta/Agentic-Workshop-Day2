# Addendum: technical choices and rationale

Downstream reference for architecture and build. These choices are kept out of the PRD on purpose.

## Stack (from the workshop's AGENTS.md and the spec)

- **Agent:** one LangChain `create_agent` agent, calling MCP tools through `langchain-mcp-adapters`.
- **Models:**
  - The agent runs on Gemini through `ChatGoogleGenerativeAI` (`MODEL`, `GEMINI_API_KEY`). `PROVIDER=groq` switches it to `ChatGroq`.
  - The judge runs on Groq (`JUDGE_MODEL`).
- **MCP tools:** `get_claim`, `get_employee`, `get_policy_limits`, and `record_decision(line_id, decision, clause, explanation)`.
  - The `explanation` argument is a deliberate addition to the signature in `BRIEF.md`.
  - The tool contract is in the spec's `mcp-tools.md`.
- **Storage:** SQLite at `cases/expense/app.db`. MLflow runs on `sqlite:///mlflow.db`.

## Rejected alternatives

- **The LLM decides.** Rejected because it can't guarantee consistency. The deterministic engine matches all 119 labels.
- **Limits from the employee's home city.** Rejected because it matches only 109 of the 119 labels.
- **Day totals that leave out rejected items.** Rejected because it matches only 117 of the 119 labels.
- **Day totals that count later claims.** Rejected because a later claim could change a decision already recorded or released.
- **An interrupt mid-run for the $500 gate.** Rejected in favor of a pending status, so an agent run never blocks.
- **Flag resolution in the MVP (FR-8).** Cut after the party review, and deferred to v2:
  - No metric or eval check validates it.
  - It opens a second path into `pending_approval`.
  - It needs rules for who resolves a flag and whether a resolution can be undone.
  - The 3:00 deadline favors hardening Review, Re-review and Release.
- **An overnight batch or the CLI as Ana's way in.** Rejected in favor of a **Review** button. It makes the holdout demo the same thing as UJ-1. It also brings in claim states, background runs and one run per claim at a time.
- **A 90% judge-faithfulness target.** Replaced by a code check against the engine's facts. The 90% number had no evidence behind it.
