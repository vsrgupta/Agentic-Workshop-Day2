"""The finance reviewer's dashboard for expense claims.

Run: uv run streamlit run cases/expense/dashboard.py

All logic lives in `dashboard_data.py`; this file is layout only. The dashboard starts
reviews in the background and shows what the agent recorded. It never decides a line.
Its only writes are the reviewer's own actions (Release, Re-review, Unstick) through
`dashboard_data`. Claim text is untrusted, so it and every message are shown as plain text.
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
approver = dd.approver_name()

if approver:
    st.text(f"Approver: {approver}")
else:
    st.warning(
        "No approver is configured. Set APPROVER_NAME in .env and restart the dashboard; "
        "until then Release is refused."
    )

# One plain-text message from the last action, shown once after its rerun.
notice = st.session_state.pop("notice", None)
if notice:
    st.text(notice)


def _notify(message: str) -> None:
    """Keep a plain-text message for the next run, then rerun (st.rerun does not return)."""
    st.session_state["notice"] = message
    st.rerun()


def _snapshot() -> tuple:
    """Claim states, decision count and recorded errors, so a finished or progressing run shows."""
    return dd.progress(db), tuple(sorted(dd.review_errors(db).items())), tuple(sorted(dd.running(db)))


tab_claims, tab_view, tab_flags, tab_approvals = st.tabs(["Claims", "Claim view", "Flags", "Approvals"])

# --- Claims list -----------------------------------------------------------------------

with tab_claims:
    counts = {}
    for c in claims:
        counts[c["state"]] = counts.get(c["state"], 0) + 1
    st.caption(" · ".join(f"{state}: {n}" for state, n in sorted(counts.items())))

    widths = [1.2, 2.2, 1.4, 0.8, 1.2, 1.2, 1.2]
    header = st.columns(widths)
    for col, label in zip(header, ["Claim", "Employee", "Submitted", "Lines", "State", "Action", "Recovery"]):
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
        recovery = dd.recovery_actions(c["state"], live=cid in live)
        if "Re-review" in recovery and row[6].button("Re-review", key=f"rereview-{cid}"):
            result = dd.re_review(cid, db)
            if result["status"] == "started":
                _notify(f"Re-review of {cid} started; its unreleased decisions were cleared.")
            elif result["status"] == "no_model":
                _notify(f"Re-review of {cid} not started; nothing was changed. {result['error']}")
            else:
                _notify(f"Re-review of {cid} was not allowed in its current state.")
        if "Unstick" in recovery and row[6].button("Unstick", key=f"unstick-{cid}"):
            st.session_state["confirm-unstick"] = cid
            st.rerun()
        if st.session_state.get("confirm-unstick") == cid and "Unstick" not in recovery:
            st.session_state.pop("confirm-unstick", None)  # stale: Unstick no longer applies
        if st.session_state.get("confirm-unstick") == cid:
            st.warning(
                "A live run may still be going for this claim (for example in another process). "
                "Unstick moves it to incomplete so Retry works; it changes no decisions."
            )
            st.text(f"Unstick {cid}?")
            confirm, cancel = st.columns(2)
            if confirm.button("Confirm unstick", key=f"unstick-confirm-{cid}"):
                st.session_state.pop("confirm-unstick", None)
                result = dd.unstick(cid, db)
                if result["status"] == "unstuck":
                    _notify(f"{cid} is now incomplete; use Retry to finish it.")
                else:
                    _notify(f"Unstick of {cid} was not allowed in its current state.")
            if cancel.button("Cancel", key=f"unstick-cancel-{cid}"):
                st.session_state.pop("confirm-unstick", None)
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
                    "Released by": l["released_by"] or "",
                    "Released at": l["released_at"] or "",
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

# --- Approvals: approved items over $500 wait for a person's Release ------------------------

with tab_approvals:
    queue = dd.approval_queue(db)
    if not queue:
        st.info("Nothing is waiting for approval.")
    else:
        st.caption(f"{len(queue)} approved item(s) over $500 waiting. Release changes a status; no money moves.")
        widths = [1.0, 1.0, 1.6, 1.0, 0.7, 3.5, 1.0]
        header = st.columns(widths)
        for col, label in zip(header, ["Claim", "Line", "Merchant", "Amount", "Clause", "Explanation", "Action"]):
            col.markdown(f"**{label}**")
        for item in queue:
            row = st.columns(widths)
            row[0].text(item["claim_id"])
            row[1].text(item["line_id"])
            row[2].text(item["merchant"])
            row[3].text(item["amount"])
            row[4].text(item["clause"] or "")
            row[5].text(item["explanation"] or "")
            if row[6].button("Release", key=f"release-{item['line_id']}", disabled=approver is None):
                result = dd.release(item["line_id"], db)
                if result["status"] == "released":
                    _notify(f"Released {item['line_id']} as {result['released_by']} at {result['released_at']}.")
                elif result["status"] == "no_approver":
                    _notify("Release refused: no approver is configured. Set APPROVER_NAME in .env.")
                else:
                    _notify(f"{item['line_id']} is no longer pending approval; nothing was changed.")

# --- Auto-refresh while a review runs ------------------------------------------------------

if "reviewing" in states.values() or live:
    st.caption(f"A review is running; this page refreshes every {REFRESH_SECONDS} seconds.")
    rendered = _snapshot()

    @st.fragment(run_every=REFRESH_SECONDS)
    def _auto_refresh():
        if _snapshot() != rendered:
            st.rerun(scope="app")

    _auto_refresh()
