from math import exp

def score_job(text: str, keywords: list[str]) -> float:
    if not text:
        return 0.0
    t = text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in t)
    return 1.0 - exp(-hits / 6.0)

def classify_track(title: str, desc: str, tracks: dict) -> tuple[str, float]:
    blob = f"{title or ''}\n{desc or ''}"
    best_name, best_score = "unknown", 0.0
    for name, cfg in tracks.items():
        kws = cfg.get("title_keywords", []) + cfg.get("skill_keywords", [])
        s = score_job(blob, kws) * float(cfg.get("weight", 1.0))
        if s > best_score:
            best_name, best_score = name, s
    return best_name, best_score
