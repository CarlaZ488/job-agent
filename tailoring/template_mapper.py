from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"

TRACK_TEMPLATE_MAP = {
    "data": TEMPLATES_DIR / "data_resume.docx",
    "it": TEMPLATES_DIR / "it_resume.docx",
    "software": TEMPLATES_DIR / "software_resume.docx",
    "ml_ai": TEMPLATES_DIR / "ml_ai_resume.docx",
    "unknown": TEMPLATES_DIR / "data_resume.docx",
}

SECTION_ALIASES = {
    "summary": ["SUMMARY", "Summary"],
    "education": ["EDUCATION", "Education"],
    "skills": ["SKILLS", "Skills"],
    "experience": ["PROFESSIONAL EXPERIENCE", "EXPERIENCE", "Professional Experience", "Experience"],
}

def get_template_path(track: str) -> Path:
    track = (track or "unknown").lower()
    return TRACK_TEMPLATE_MAP.get(track, TRACK_TEMPLATE_MAP["unknown"])