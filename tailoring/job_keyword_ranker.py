import re
from collections import Counter

STOPWORDS = {
    "and", "the", "for", "with", "that", "this", "from", "into", "using", "used",
    "your", "our", "their", "you", "are", "will", "have", "has", "had", "its",
    "job", "role", "team", "work", "works", "working", "experience", "skills",
    "ability", "required", "preferred", "including", "support", "responsible",
    "strong", "ability", "candidate", "position", "applications", "application",
    "remote", "full", "time"
}

PHRASES = [
    "machine learning", "data engineering", "data analysis", "technical support",
    "system administration", "power bi", "microsoft fabric", "azure devops",
    "rest api", "data pipelines", "lakehouse architecture", "feature engineering",
    "cross validation", "azure machine learning", "openai api", "fastapi",
    ".net", "sql", "python", "react", "javascript", "linux", "mongodb",
    "postgresql", "jupyter notebooks", "cloud development", "authentication"
]

def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("→", " ")
    text = re.sub(r"[^a-z0-9\+\#\.\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def extract_keywords(text: str, top_n: int = 25) -> list[str]:
    text = normalize_text(text)

    found_phrases = []
    for phrase in PHRASES:
        if phrase in text:
            found_phrases.append(phrase)

    words = re.findall(r"[a-z0-9\+\#\.\-]{2,}", text)
    words = [w for w in words if w not in STOPWORDS and not w.isdigit()]

    counts = Counter(words)
    ranked_words = [w for w, _ in counts.most_common(top_n)]

    seen = set()
    out = []
    for item in found_phrases + ranked_words:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out[:top_n]

def score_text_against_keywords(text: str, keywords: list[str]) -> float:
    blob = normalize_text(text)
    score = 0.0
    for kw in keywords:
        if kw in blob:
            score += 2.0 if " " in kw else 1.0
    return score