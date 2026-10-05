"""The finance reviewer's dashboard for expense claims.

Run: uv run streamlit run cases/expense/dashboard.py

All logic lives in `dashboard_data.py`; this file is layout only. The dashboard starts
reviews in the background and shows what the agent recorded. It never decides a line
and never writes a decision. Claim text is untrusted, so it is shown as plain data.
"""

import sys
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parent
if str(CASE_DIR) not in sys.path:
    sys.path.insert(0, str(CASE_DIR))

import streamlit as st  # noqa: E402

import dashboard_data as dd  # noqa: E402

REFRESH_SECONDS = 3

st.set_page_config(page_title="Expense claim reviewer", layout="wide")
dd.setup_mlflow()

db = dd.resolve_db()
st.title("Expense claim reviewer")

try:
    claims = dd.list_claims(db)
except FileNotFoundError:
    st.error("No database yet. Load it first: uv run python cases/expense/load_seed.py")
    st.stop()

errors = dd.review_errors(db)
live = dd.running(db)
states = {c["claim_id"]: c["state"] for c in claims}


def _snapshot() -> tuple:
    """Claim states, decision count and recorded errors, so a finished or progressing run shows."""
    return dd.progress(db), tuple(sorted(dd.review_errors(db).items())), tuple(sorted(dd.running(db)))


tab_claims, tab_view, tab_flags = st.tabs(["Claims", "Claim view", "Flags"])

# --- Claims list -----------------------------------------------------------------------

with tab_claims:
    counts = {}
    for c in claims:
        counts[c["state"]] = counts.get(c["state"], 0) + 1
    st.caption(" · ".join(f"{state}: {n}" for state, n in sorted(counts.items())))

    widths = [1.2, 2.2, 1.4, 0.8, 1.2, 1.2]
    header = st.columns(widths)
    for col, label in zip(header, ["Claim", "Employee", "Submitted", "Lines", "State", "Action"]):
        col.markdown(f"**{label}**")

    for c in claims:
        cid = c["claim_id"]
        row = st.columns(widths)
        row[0].text(cid)
        employee = c["employee_id"] + (f" ({c['employee_name']})" if c.get("employee_name") else "")
        row[1].text(employee)
        row[2].text(c["submitted_at"])
        row[3].text(str(c["line_count"]))
        row[4].text(c["state"])
        action = None if cid in live else dd.action_for(c["state"])
        if cid in live and c["state"] != "reviewing":
            row[5].text("starting")
        if action and row[5].button(action, key=f"start-{cid}"):
            dd.start_review(cid, db)
            st.rerun()
        if cid in errors and cid not in live and c["state"] in ("waiting", "incomplete"):
            st.error(f"The last review of {cid} failed:")
            st.text(errors[cid])  # plain text: the message can carry untrusted claim text

# --- Claim view ------------------------------------------------------------------------

with tab_view:
    claim_ids = [c["claim_id"] for c in claims]
    selected = st.selectbox("Claim", claim_ids, key="claim-view") if claim_ids else None
    if selected:
        st.text(f"State: {states[selected]}")
        lines = dd.claim_lines(selected, db)
        st.dataframe(
            [
                {
                    "Line": l["line_id"],
                    "Date": l["date"],
                    "Category": l["category"],
                    "Merchant": l["merchant"],
                    "Amount": l["amount"],
                    "Decision": l["decision"],
                    "Clause": l["clause"] or "",
                    "Explanation": l["explanation"] or "",
                    "Payout": l["payout_status"],
                    "Agent disagrees": "DISAGREES" if l["agent_disagrees"] else "",
                }
                for l in lines
            ],
            hide_index=True,
            width="stretch",
        )
        disputed = [l["line_id"] for l in lines if l["agent_disagrees"]]
        if disputed:
            st.warning("The agent disagrees with the engine on: " + ", ".join(disputed))

# --- Flags (read-only) -------------------------------------------------------------------

with tab_flags:
    flags = dd.list_flags(db)
    if not flags:
        st.info("No flagged line items yet.")
    else:
        st.dataframe(
            [
                {
                    "Claim": f["claim_id"],
                    "Line": f["line_id"],
                    "Category": f["category"],
                    "Merchant": f["merchant"],
                    "Amount": f["amount"],
                    "Clause or reason": f["clause"] or f["reason"] or "",
                    "Engine facts": ", ".join(f"{k}={v}" for k, v in f["facts"].items()),
                }
                for f in flags
            ],
            hide_index=True,
            width="stretch",
        )

# --- Auto-refresh while a review runs ------------------------------------------------------

if "reviewing" in states.values() or live:
    st.caption(f"A review is running; this page refreshes every {REFRESH_SECONDS} seconds.")
    rendered = _snapshot()

    @st.fragment(run_every=REFRESH_SECONDS)
    def _auto_refresh():
        if _snapshot() != rendered:
            st.rerun(scope="app")

    _auto_refresh()
