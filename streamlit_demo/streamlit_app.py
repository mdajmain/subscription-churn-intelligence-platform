"""
Churn risk calculator -- a Streamlit front end over the same trained model
artifact the FastAPI service and the daily batch job use.

Scoring goes through api/scoring.py's score_rows() rather than re-implementing
preprocessing here, so this app cannot silently disagree with the API or the
batch job about what a given subscriber's risk is.

Run locally:   streamlit run streamlit_demo/streamlit_app.py
"""

from pathlib import Path
import sys

import pandas as pd
import streamlit as st

# repo root is one level up -- lets this app import api.scoring and find
# models/artifacts/churn_model.joblib, both of which live there.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.scoring import score_rows  # noqa: E402

st.set_page_config(page_title="Churn Risk Calculator", page_icon="📡", layout="wide")

# Plan list prices as they actually appear in the data (see fct_prediction).
PLAN_PRICES = {7: 25.0, 30: 99.0, 90: 280.0, 180: 520.0, 410: 1788.0}

# Plain-English names for the model's feature columns. The preprocessor
# prefixes them (num__/bool__/cat__), stripped before lookup.
FEATURE_LABELS = {
    "active_days_7d": "Days active in the last week",
    "active_days_30d": "Days active in the last month",
    "active_days_90d": "Days active in the last 3 months",
    "total_secs_7d": "Listening time this week",
    "total_secs_30d": "Listening time this month",
    "total_secs_90d": "Listening time over 3 months",
    "is_auto_renew": "Auto-renew setting",
    "actual_amount_paid": "Amount paid at last renewal",
    "plan_list_price": "Plan list price",
    "discount": "Discount applied",
    "days_since_last_activity": "Days since they last opened the app",
    "tenure_since_registration_days": "How long they've been a member",
    "prior_renewal_count": "Number of past renewals",
    "prior_cancellation_count": "Number of past cancellations",
    "distinct_payment_methods_used": "Payment methods used",
    "prior_plan_changes": "Past plan changes",
    "num_unq_30d": "Different songs played this month",
    "activity_ratio_7d_to_30d_avg": "This week's listening vs. their usual",
    "share_plays_98_5plus_30d": "Share of songs played to the end",
    "share_plays_below25_30d": "Share of songs skipped early",
    "payment_plan_days": "Plan length",
    "missing_activity_flag": "No activity history on record",
    "bd_is_valid": "Age looks valid",
    "bd": "Age",
    "city": "City",
    "gender": "Gender",
    "registered_via": "How they signed up",
    "payment_method_id": "Payment method",
}

PRESETS = {
    "Healthy regular": dict(
        plan=30, auto_renew=True, tenure_months=24, ad7=6, ad30=24, ad90=70,
        hrs7=9.0, hrs30=38.0, hrs90=115.0, days_since=1, renewals=18, cancels=0,
    ),
    "Going quiet": dict(
        plan=30, auto_renew=False, tenure_months=8, ad7=0, ad30=4, ad90=28,
        hrs7=0.0, hrs30=3.0, hrs90=30.0, days_since=11, renewals=3, cancels=1,
    ),
    "Brand new, unsure": dict(
        plan=7, auto_renew=False, tenure_months=1, ad7=2, ad30=5, ad90=5,
        hrs7=1.5, hrs30=4.0, hrs90=4.0, days_since=4, renewals=1, cancels=0,
    ),
    "Long-haul loyalist": dict(
        plan=410, auto_renew=True, tenure_months=34, ad7=5, ad30=21, ad90=62,
        hrs7=7.0, hrs30=30.0, hrs90=88.0, days_since=2, renewals=4, cancels=0,
    ),
}


def apply_preset(name: str) -> None:
    for k, v in PRESETS[name].items():
        st.session_state[k] = v


# Default state, before any widget is created.
for k, v in PRESETS["Going quiet"].items():
    st.session_state.setdefault(k, v)


st.title("Will this subscriber churn?")
st.caption(
    "Enter what you know about a subscriber and the trained model returns their "
    "probability of cancelling within the next 30 days — plus what drove that number."
)

with st.sidebar:
    st.subheader("Start from an example")
    st.caption("Loads a realistic subscriber you can then edit.")
    for name in PRESETS:
        st.button(name, key=f"preset_{name}", use_container_width=True,
                  on_click=apply_preset, args=(name,))
    st.divider()
    st.caption(
        "Model: XGBoost, probability-calibrated (sigmoid). Trained on 6,000 "
        "subscriber histories with a strict point-in-time cutoff — no future "
        "data ever visible to a feature."
    )

left, right = st.columns([3, 2], gap="large")

with left:
    st.subheader("The subscription")
    c1, c2, c3 = st.columns(3)
    with c1:
        plan = st.selectbox("Plan length", list(PLAN_PRICES), key="plan",
                            format_func=lambda d: f"{d}-day")
    with c2:
        list_price = PLAN_PRICES[plan]
        paid = st.number_input("Amount they paid", min_value=0.0, max_value=2000.0,
                               value=float(list_price), step=1.0,
                               help=f"List price for a {plan}-day plan is ${list_price:,.0f}.")
    with c3:
        auto_renew = st.toggle("Auto-renew is on", key="auto_renew")

    c4, c5, c6 = st.columns(3)
    with c4:
        tenure_months = st.slider("Member for (months)", 0, 40, key="tenure_months")
    with c5:
        renewals = st.number_input("Past renewals", 0, 40, key="renewals")
    with c6:
        cancels = st.number_input("Past cancellations", 0, 10, key="cancels")

    st.subheader("How they've been using it")
    c7, c8, c9 = st.columns(3)
    with c7:
        ad7 = st.slider("Days active, last 7 days", 0, 7, key="ad7")
        hrs7 = st.number_input("Listening hours, last 7 days", 0.0, 80.0, step=0.5, key="hrs7")
    with c8:
        ad30 = st.slider("Days active, last 30 days", 0, 30, key="ad30")
        hrs30 = st.number_input("Listening hours, last 30 days", 0.0, 300.0, step=1.0, key="hrs30")
    with c9:
        ad90 = st.slider("Days active, last 90 days", 0, 90, key="ad90")
        hrs90 = st.number_input("Listening hours, last 90 days", 0.0, 900.0, step=1.0, key="hrs90")

    days_since = st.slider("Days since they last opened the app", 0, 365, key="days_since")

    # This distinction matters more than it looks. "We have usage data and it
    # shows zero" is a strong churn signal (~83%). "We have no usage data at
    # all" is not (~9%) -- in the training data those rows churn at 7.8%, the
    # base rate, because missing logs mean missing *data*, not a quiet user.
    # Inferring one from the other would invert the prediction.
    no_activity = st.checkbox(
        "We have no usage data at all for this subscriber",
        value=False,
        help="Tick this only if usage was never recorded — not when it was recorded as zero. "
             "Zero recorded usage is a strong churn signal; no data at all is not.",
    )

    with st.expander("Optional details (sensible defaults already applied)"):
        d1, d2, d3 = st.columns(3)
        with d1:
            age = st.number_input("Age", 0, 100, 30)
            gender = st.selectbox("Gender", ["unknown", "male", "female"])
        with d2:
            songs = st.number_input("Different songs played this month", 0, 600, 110)
            finished = st.slider("Share of songs played to the end", 0.0, 1.0, 0.47)
        with d3:
            skipped = st.slider("Share of songs skipped early", 0.0, 1.0, 0.24)
            methods = st.number_input("Payment methods used", 1, 6, 1)
        plan_changes = st.number_input("Past plan changes", 0, 5, 0)

# --- sanity warnings: impossible combinations produce meaningless predictions ---
problems = []
if ad30 < ad7:
    problems.append("Days active in the last month can't be fewer than in the last week.")
if ad90 < ad30:
    problems.append("Days active in the last 3 months can't be fewer than in the last month.")
if hrs30 < hrs7:
    problems.append("Listening hours this month can't be fewer than this week's.")
if hrs90 < hrs30:
    problems.append("Listening hours over 3 months can't be fewer than this month's.")
if ad7 > 0 and days_since > 7:
    problems.append("They can't be active in the last 7 days and also not have opened the app in over a week.")

secs7, secs30, secs90 = hrs7 * 3600, hrs30 * 3600, hrs90 * 3600

row = {
    "bd": age,
    "payment_plan_days": plan,
    "plan_list_price": list_price,
    "actual_amount_paid": paid,
    "discount": list_price - paid,
    "tenure_since_registration_days": int(tenure_months * 30.44),
    "prior_renewal_count": renewals,
    "prior_cancellation_count": cancels,
    "distinct_payment_methods_used": methods,
    "prior_plan_changes": plan_changes,
    "total_secs_7d": secs7,
    "total_secs_30d": secs30,
    "total_secs_90d": secs90,
    "num_unq_30d": songs,
    "active_days_7d": ad7,
    "active_days_30d": ad30,
    "active_days_90d": ad90,
    # matches fct_prediction.sql: NULL only when there is genuinely no activity history
    "days_since_last_activity": None if no_activity else days_since,
    "activity_ratio_7d_to_30d_avg": ((secs7 / 7.0) / (secs30 / 30.0)) if secs30 > 0 else None,
    "share_plays_98_5plus_30d": finished if secs30 > 0 else None,
    "share_plays_below25_30d": skipped if secs30 > 0 else None,
    "bd_is_valid": 0 < age < 100,
    "is_auto_renew": auto_renew,
    "missing_activity_flag": no_activity,
    "city": 10,
    "gender": gender if gender != "unknown" else None,
    "registered_via": 3,
    "payment_method_id": 4,
}

result = score_rows([row])[0]
prob = result["calibrated_churn_probability"]

if prob >= 0.70:
    tier, color, action = "High risk", "#d03b3b", "Worth a retention offer before the renewal date."
elif prob >= 0.30:
    tier, color, action = "Medium risk", "#c98816", "Worth a nudge — a re-engagement email or a reminder of what they're not using."
else:
    tier, color, action = "Low risk", "#0ca30c", "No action needed. Spend retention budget elsewhere."

with right:
    st.subheader("Prediction")
    if problems:
        for p in problems:
            st.warning(p, icon="⚠️")

    st.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,.3);border-radius:14px;padding:22px;text-align:center">
          <div style="font-size:3.4rem;font-weight:600;line-height:1;color:{color}">{prob*100:.1f}%</div>
          <div style="font-size:.95rem;opacity:.75;margin-top:6px">chance of cancelling within 30 days</div>
          <div style="display:inline-block;margin-top:12px;padding:4px 14px;border-radius:100px;
                      background:{color}22;color:{color};font-weight:600;font-size:.85rem">{tier}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.progress(min(1.0, prob))
    st.caption(f"**What to do:** {action}")

    # Revenue exposure, same definition as mart_risk_exposure.sql: churn
    # probability x the renewal's value normalised to a 30-day period. A $0
    # payment is excluded there rather than shown as zero exposure.
    if paid > 0:
        exposure = prob * (paid / plan * 30)
        st.metric("Revenue at risk over 30 days", f"${exposure:,.2f}",
                  help="Churn probability x what this subscriber is worth over a 30-day period.")
    else:
        st.metric("Revenue at risk over 30 days", "n/a",
                  help="No revenue figure for a $0 payment — excluded rather than counted as zero.")

    st.subheader("Why")
    st.caption("The three things that moved this prediction the most.")
    for item in result["top_contributing_features"]:
        name, value = item.rsplit(":", 1)
        clean = name.split("__", 1)[-1]
        value = float(value)
        label = FEATURE_LABELS.get(clean, clean.replace("_", " "))
        if value > 0:
            st.markdown(f"⬆️ **{label}** — pushing risk *up*")
        else:
            st.markdown(f"⬇️ **{label}** — pulling risk *down*")

    st.caption(f"Model version: `{result['model_version']}`")

st.divider()
st.caption(
    "Built on 6,000 simulated subscriber histories modelled on real subscription-streaming "
    "behaviour — not real people's data. Predictions come from the same model artifact and "
    "scoring path as the project's FastAPI service and nightly batch job."
)
