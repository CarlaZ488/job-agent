import subprocess
import sys

def run(cmd):
    print(f"\n>>> {' '.join(cmd)}\n")
    subprocess.check_call(cmd)

if __name__ == "__main__":
    py = sys.executable

    run([py, "ingestion/gmail_ingest.py"])
    run([py, "-m", "enrichment.enrich_jobs"])
    run([py, "maintenance/dedupe_by_apply_url.py"])

    print("\n✅ Pipeline complete.\n")