# Job Agent (Human-in-the-loop)

This starter repo helps you:
1) Ingest jobs (USAJOBS API + email alert parsing stubs)
2) Score and queue them (Data/Software/IT)
3) Track everything in a Streamlit dashboard
4) Autofill applications and STOP before submit (Playwright skeleton)

## Quickstart
1) Create venv and install:
   pip install -r requirements.txt
   playwright install

2) Start dashboard:
   streamlit run dashboard/streamlit_app.py

3) Ingest (stub):
   python ingestion/usajobs_api.py

## Notes
- We intentionally avoid full scraping of LinkedIn; prefer email alerts parsing.
- The system is designed to stop before final submit.
