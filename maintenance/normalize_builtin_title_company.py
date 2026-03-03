import re
import sqlite3

DB_PATH = "database/jobs.db"

def split_builtin_title(full: str):
    """
    Built In commonly formats: 'Role - Company | Built In'
    """
    if not full:
        return None, None

    t = full.strip()

    # Remove trailing site marker
    t = re.sub(r"\s*\|\s*Built\s*In\s*$", "", t, flags=re.IGNORECASE)

    # Split on first " - " into role + company
    if " - " in t:
        role, company = t.split(" - ", 1)
        role = role.strip()
        company = company.strip()
        if role and company:
            return role, company

    return None, None

def main(dry_run=True):
    con = sqlite3.connect(DB_PATH)

    rows = con.execute("""
        SELECT id, title, company, url
        FROM jobs
        WHERE status <> 'archived'
          AND (company IS NULL OR company = '' OR title LIKE '%| Built In%')
        ORDER BY id DESC
    """).fetchall()

    updates = 0
    for job_id, title, company, url in rows:
        # Only normalize when the title looks like Built In formatting
        if not title or "built in" not in title.lower():
            continue

        role, comp = split_builtin_title(title)
        if not role or not comp:
            continue

        new_title = role
        new_company = company or comp  # don't overwrite existing company

        if dry_run:
            print(f"#{job_id}: '{title}' -> title='{new_title}', company='{new_company}'")
        else:
            con.execute(
                "UPDATE jobs SET title=?, company=? WHERE id=?",
                (new_title, new_company, job_id),
            )
            updates += 1

    if not dry_run:
        con.commit()
        print(f"Done. Updated {updates} jobs.")
    else:
        print("Dry run only. Re-run with dry_run=False to apply.")

    con.close()

if __name__ == "__main__":
    main(dry_run=False)