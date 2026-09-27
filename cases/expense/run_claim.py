"""Review one expense claim with the agent and print the result as JSON.

Usage: uv run python cases/expense/run_claim.py CL-2001
"""

import asyncio
import json
import sys

import mlflow
from dotenv import load_dotenv

from review import review_claim

TRACKING_URI = "sqlite:///mlflow.db"
EXPERIMENT = "expense-reviewer"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: uv run python cases/expense/run_claim.py <claim_id>", file=sys.stderr)
        return 2
    claim_id = args[0]

    load_dotenv()
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT)
    mlflow.langchain.autolog()

    try:
        with mlflow.start_span(name="review_claim", span_type="AGENT") as span:
            span.set_inputs({"claim_id": claim_id})
            result = asyncio.run(review_claim(claim_id))
            span.set_outputs(result)
    except KeyError as exc:
        print(f"Error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: the review of {claim_id} failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("complete", "already_complete") else 1


if __name__ == "__main__":
    sys.exit(main())
