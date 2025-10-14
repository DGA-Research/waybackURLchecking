import io
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

import check_tweet_links as ctl


def read_uploaded_file(uploaded_file: st.runtime.uploaded_file_manager.UploadedFile) -> pd.DataFrame:
    if uploaded_file.name.lower().endswith(".csv"):
        return pd.read_csv(uploaded_file)
    return pd.read_excel(uploaded_file)


def run_checks(df: pd.DataFrame, url_column: str, timeout: float, sleep: float, progress_every: int) -> pd.DataFrame:
    total_rows = len(df)
    progress_bar = st.progress(0)
    status_text = st.empty()

    oembed_session = requests.Session()
    bot_session = requests.Session()
    bot_session.headers.update(
        {"User-Agent": "Mozilla/5.0 (compatible; Twitterbot/1.0; +https://twitter.com/twitterbot)"}
    )

    cache = {}
    results = []
    checked_at = datetime.now(timezone.utc).isoformat()

    for index, url in enumerate(df[url_column], start=1):
        tweet_id = ctl.extract_tweet_id(url)

        if not tweet_id:
            results.append(
                {
                    "tweet_id": None,
                    "availability": "invalid_url",
                    "http_status": None,
                    "detail": "Tweet ID not found in URL",
                    "checked_at": checked_at,
                    "oembed_status": None,
                    "checked_url": None,
                }
            )
        else:
            if tweet_id not in cache:
                cache[tweet_id] = ctl.check_tweet(tweet_id, url, oembed_session, bot_session, timeout)
                if sleep:
                    time.sleep(sleep)

            result = cache[tweet_id]
            results.append(
                {
                    "tweet_id": tweet_id,
                    "availability": result.get("availability"),
                    "http_status": result.get("http_status"),
                    "detail": result.get("detail"),
                    "checked_at": checked_at,
                    "oembed_status": result.get("oembed_status"),
                    "checked_url": result.get("checked_url"),
                }
            )

        if progress_every and index % progress_every == 0:
            status_text.text(f"Checked {index}/{total_rows} rows...")

        progress_bar.progress(index / total_rows)

    status_text.text(f"Completed {total_rows} rows.")
    progress_bar.empty()

    return pd.concat([df.reset_index(drop=True), pd.DataFrame(results)], axis=1)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer.getvalue()


st.set_page_config(page_title="Tweet Availability Checker", layout="wide")
st.title("Tweet Availability Checker")
st.write("Upload a CSV or Excel file containing tweet URLs to verify whether the tweets are still accessible.")

uploaded = st.file_uploader("Upload file", type=["csv", "xlsx", "xls"])

if uploaded:
    try:
        data = read_uploaded_file(uploaded)
    except Exception as exc:  # pylint: disable=broad-except
        st.error(f"Could not read the uploaded file: {exc}")
        st.stop()

    if data.empty:
        st.warning("The uploaded file has no data.")
        st.stop()

    url_column = st.selectbox("Select the column that contains tweet URLs", data.columns.tolist())
    timeout = st.number_input("Request timeout (seconds)", min_value=1.0, value=10.0, step=1.0)
    sleep = st.number_input("Delay between requests (seconds)", min_value=0.0, value=0.0, step=0.1)
    progress_every = st.number_input(
        "Show progress update every N rows (0 to disable)", min_value=0, value=25, step=1
    )

    if st.button("Check Tweets", type="primary"):
        with st.spinner("Checking tweet availability..."):
            combined = run_checks(data, url_column, timeout, sleep, progress_every)

        st.success("Completed tweet availability checks.")
        st.dataframe(combined)

        counts = combined["availability"].value_counts(dropna=False)
        st.subheader("Availability Summary")
        st.write(counts)

        excel_bytes = to_excel_bytes(combined)
        st.download_button(
            "Download results as Excel",
            data=excel_bytes,
            file_name=f"{uploaded.name.rsplit('.', 1)[0]}_checked.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        csv_bytes = combined.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download results as CSV",
            data=csv_bytes,
            file_name=f"{uploaded.name.rsplit('.', 1)[0]}_checked.csv",
            mime="text/csv",
        )
