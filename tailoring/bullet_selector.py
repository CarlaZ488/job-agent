import json
from pathlib import Path
from tailoring.job_keyword_ranker import score_text_against_keywords

ROOT = Path(__file__).resolve().parents[1]
BULLET_BANK_PATH = ROOT / "config" / "bullet_bank.json"

def _flatten(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_flatten(v))
        return out
    if isinstance(value, dict):
        out = []
        for v in value.values():
            out.extend(_flatten(v))
        return out
    return []

def load_bullet_bank(path: Path = BULLET_BANK_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def bullets_for_track(bank: dict, track: str) -> list[str]:
    track = (track or "unknown").lower()

    if track == "ml_ai":
        pools = [
            _flatten(bank.get("ml_ai", [])),
            _flatten(bank.get("data", [])),
            _flatten(bank.get("software", [])),
            _flatten(bank.get("general", [])),
        ]
    else:
        pools = [
            _flatten(bank.get(track, [])),
            _flatten(bank.get("general", [])),
        ]

    seen = set()
    out = []
    for pool in pools:
        for bullet in pool:
            b = bullet.strip()
            if b and b not in seen:
                out.append(b)
                seen.add(b)
    return out

def select_top_bullets(track: str, keywords: list[str], top_n: int = 5) -> list[str]:
    bank = load_bullet_bank()
    bullets = bullets_for_track(bank, track)
    scored = [(score_text_against_keywords(b, keywords), b) for b in bullets]
    scored.sort(key=lambda x: x[0], reverse=True)

    selected = [b for s, b in scored if s > 0][:top_n]
    if not selected:
        selected = bullets[:top_n]
    return selected