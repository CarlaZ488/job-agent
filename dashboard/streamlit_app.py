import sqlite3
import pandas as pd
import streamlit as st

DB_PATH = r"/mnt/data/job_agent/database/jobs.db"

st.set_page_config(page_title="Job Agent Dashboard", layout="wide")
st.title("Job Agent Dashboard")
st.caption("Data (1.0) • Software (0.85) • IT (0.75) — Review → Submit")

def load_df(query: str, params=()):
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(query, con, params=params)
    con.close()
    return df

status = st.multiselect(
    "Status",
    ["new","queued","applying","applied","interview","offer","rejected","archived"],
    default=["new","queued"]
)
track = st.multiselect("Track", ["data","software","it","mixed","unknown"], default=["data","software","it"])

col1, col2, col3 = st.columns(3)
with col1:
    min_score = st.slider("Min match score", 0.0, 1.0, 0.55, 0.01)
with col2:
    max_distance = st.slider("Max distance (miles)", 0, 50, 18, 1)
with col3:
    max_commute = st.slider("Max commute (minutes)", 0, 180, 90, 5)

status_placeholders = ",".join(["?"] * len(status))
track_placeholders = ",".join(["?"] * len(track))

q = f"""SELECT id, title, company, location_text, distance_miles, commute_minutes, work_mode,
                track, match_score, salary_suggested, posted_date, status, url
         FROM jobs
         WHERE status IN ({status_placeholders})
           AND track IN ({track_placeholders})
           AND (match_score IS NULL OR match_score >= ?)
           AND (distance_miles IS NULL OR distance_miles <= ?)
           AND (commute_minutes IS NULL OR commute_minutes <= ?)
         ORDER BY (match_score IS NULL), match_score DESC, posted_date DESC, scraped_at DESC
      """

df = load_df(
    q.format(status_placeholders=status_placeholders, track_placeholders=track_placeholders),
    tuple(status) + tuple(track) + (min_score, max_distance, max_commute)
)

st.subheader("Queue")
st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Quick status update")

if not df.empty:
    job_id = st.selectbox("Job id", df["id"].tolist())
    new_status = st.selectbox("Set status", ["queued","applying","applied","interview","offer","rejected","archived"])
    notes = st.text_area("Notes (optional)")
    if st.button("Update"):
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE jobs SET status=?, notes=? WHERE id=?", (new_status, notes, int(job_id)))
        con.commit()
        con.close()
        st.success("Updated.")
        st.rerun()
else:
    st.info("No jobs match your filters yet. Run ingestion to populate the database.")