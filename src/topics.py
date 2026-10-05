"""Trend sinyallerinden aday video konulari uretir -> data/candidates.json"""
import os, sys, json, datetime
import requests, yaml
from trends import collect

PROMPT = """You are a research editor for an English-language YouTube channel about {theme}.
Audience: {audience}. Each video is about {minutes} minutes long.

Below are this week's trending headlines and signals (titles only, from several sources).
Do NOT copy any headline or video title. Use them only to spot what people care about.

Propose {n} ORIGINAL video topics. For each give:
- working_title: a fresh, curiosity-driven title
- angle: the unique angle that differs from typical coverage
- why_now: why it is rising this week (based on the signals)
- depth_check: can it sustain {minutes} minutes with real substance? (yes/no + one sentence)
- research_queries: 5 factual search queries to gather sources (e.g. Wikipedia, official docs)
- risk: any accuracy or policy risk (e.g. rumors, unverified claims)

Return ONLY a JSON array of {n} objects with exactly those keys.

SIGNALS:
{signals}
"""


def call_gemini(model, key, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    r = requests.post(
        url, params={"key": key}, timeout=120,
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}},
    )
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def main(profile_path="config/profile_en.yaml"):
    cfg = yaml.safe_load(open(profile_path, encoding="utf-8"))
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY tanimli degil")
    signals = collect(cfg)
    lines = "\n".join(f"- [{s['source']}] {s['title']}" for s in signals)
    prompt = PROMPT.format(theme=cfg["theme"], audience=cfg["audience"],
                           minutes=cfg["video_minutes"], n=cfg["candidates_count"],
                           signals=lines)
    candidates = call_gemini(cfg["gemini_model"], key, prompt)
    out = {"generated": datetime.date.today().isoformat(),
           "language": cfg["language"], "signals_used": len(signals),
           "candidates": candidates}
    os.makedirs("data", exist_ok=True)
    with open("data/candidates.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"{len(candidates)} aday konu yazildi ({len(signals)} sinyal).")


if __name__ == "__main__":
    main(*sys.argv[1:])
