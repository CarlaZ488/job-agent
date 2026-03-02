PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  external_id TEXT,
  url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  company TEXT,
  location_text TEXT,
  location_lat REAL,
  location_lon REAL,
  distance_miles REAL,
  commute_minutes INTEGER,
  work_mode TEXT,
  posted_date TEXT,
  scraped_at TEXT NOT NULL,
  description TEXT,
  track TEXT,
  match_score REAL,
  salary_min INTEGER,
  salary_max INTEGER,
  salary_suggested INTEGER,
  seniority TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  notes TEXT
);

CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  applied_at TEXT NOT NULL,
  resume_path TEXT,
  cover_letter_path TEXT,
  submission_method TEXT,
  outcome TEXT,
  last_update_at TEXT,
  FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_track ON jobs(track);