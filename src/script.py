"""Secilen aday konu icin Wikipedia'dan kaynak toplar, senaryo yazar.
Kullanim: python src/script.py 4   (4 = candidates.json'daki 4. konu)
Cikti: data/script.json ve data/script.md
"""
import os, sys, json, time, datetime
import requests, yaml
from topics import call_gemini

API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "weekly-video-bot/0.1 (research script)"}
MAX_SOURCES = 10
MAX_CHARS = 5000
WORDS_PER_SECTION = 270
SECTIONS = 11


def wiki_search(query, n=2):
    r = requests.get(API, headers=UA, timeout=20, params={
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": n, "format": "json"})
    r.raise_for_status()
    return [h["title"] for h in r.json()["query"]["search"]]


def wiki_extract(title):
    r = requests.get(API, headers=UA, timeout=20, params={
        "action": "query", "prop": "extracts", "explaintext": 1,
        "redirects": 1, "titles": title, "format": "json"})
    r.raise_for_status()
    page = next(iter(r.json()["query"]["pages"].values()))
    return page.get("extract", "")


def gather_sources(queries):
    sources, seen = [], set()
    for q in queries:
        try:
            titles = wiki_search(q)
        except Exception as e:
            print(f"Arama hatasi ({q}): {e}")
            continue
        for t in titles:
            if t in seen or len(sources) >= MAX_SOURCES:
                continue
            seen.add(t)
            try:
                text = wiki_extract(t)
            except Exception as e:
                print(f"Metin hatasi ({t}): {e}")
                continue
            if len(text) < 500:
                continue
            sources.append({
                "title": t,
                "url": "https://en.wikipedia.org/wiki/" + t.replace(" ", "_"),
                "text": text[:MAX_CHARS]})
    return sources


def ask(models, key, prompt, tries=3):
    for i in range(tries):
        try:
            return call_gemini(models, key, prompt)
        except (ValueError, KeyError, IndexError) as e:
            print(f"Yanit cozumlenemedi ({e}), tekrar deneniyor...")
            time.sleep(10)
    sys.exit("Gemini yaniti cozumlenemedi.")


OUTLINE_PROMPT = """You are the head writer of an English YouTube channel about {theme}.
Target audience: {audience}. Video length: about {minutes} minutes.

Topic: {title}
Angle: {angle}

Use ONLY the reference material below for facts. Plan a video of exactly {n} sections:
section 1 = a gripping hook and intro, sections 2-{m} = the body, section {n} = a closing
that summarizes and leaves the viewer with one useful takeaway.

Return ONLY a JSON object with these keys:
- title_options: 3 honest, curiosity-driven titles, each under 70 characters, no clickbait lies
- description: 2-3 sentence YouTube description summarizing the video
- hashtags: array of 5 hashtags (with #)
- thumbnail_text: at most 4 words
- sections: array of {n} objects, each with "heading" and "key_points" (array of 3-4 short points that appear in the reference material)

REFERENCE MATERIAL:
{material}
"""

SECTION_PROMPT = """You are writing the narration for one section of a YouTube video about {theme}.
Video title: {title}
Full outline: {outline}

Write section {i} of {n}: "{heading}"
Key points to cover: {points}

STRICT RULES:
- Use ONLY facts found in the reference material below. If a number, date, name or quote is not in it, do not use it.
- If something is uncertain or varies, say so plainly instead of guessing.
- Spoken English, conversational and clear, as a narrator would read aloud. About {words} words.
- No markdown, no bullet points, no stage directions, no URLs, no phrases like "in this section" or "according to the source".
- {style}

Return ONLY a JSON object with:
- narration: the text to be read aloud
- visual_keywords: array of 6 to 8 short, concrete search phrases for stock footage or photos (for example "server room", "hard drive close up"), in the order they should appear

REFERENCE MATERIAL:
{material}
"""


def main(index="1", profile_path="config/profile_en.yaml"):
    cfg = yaml.safe_load(open(profile_path, encoding="utf-8"))
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY tanimli degil")
    models = [cfg["gemini_model"]] + cfg.get(
        "fallback_models", ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"])

    data = json.load(open("data/candidates.json", encoding="utf-8"))
    cand = data["candidates"][int(index) - 1]
    print("Secilen konu:", cand["working_title"])

    sources = gather_sources(cand["research_queries"])
    if len(sources) < 3:
        sys.exit("Yeterli kaynak bulunamadi, konuyu degistirin.")
    print(f"{len(sources)} kaynak toplandi:", [s["title"] for s in sources])
    material = "\n\n".join(f"### {s['title']}\n{s['text']}" for s in sources)

    outline = ask(models, key, OUTLINE_PROMPT.format(
        theme=cfg["theme"], audience=cfg["audience"], minutes=cfg["video_minutes"],
        title=cand["working_title"], angle=cand["angle"],
        n=SECTIONS, m=SECTIONS - 1, material=material))
    sections_plan = outline["sections"]
    headings = [s["heading"] for s in sections_plan]
    title = outline["title_options"][0]

    sections = []
    for i, sec in enumerate(sections_plan, 1):
        style = ("Open with a hook that makes the viewer want to keep watching."
                 if i == 1 else
                 "Close with a clear takeaway and a short, natural sign-off."
                 if i == len(sections_plan) else
                 "Continue smoothly from the previous section without repeating it.")
        out = ask(models, key, SECTION_PROMPT.format(
            theme=cfg["theme"], title=title, outline=json.dumps(headings),
            i=i, n=len(sections_plan), heading=sec["heading"],
            points=json.dumps(sec["key_points"]), words=WORDS_PER_SECTION,
            style=style, material=material))
        sections.append({"heading": sec["heading"],
                         "narration": out["narration"].strip(),
                         "visual_keywords": out["visual_keywords"]})
        print(f"Bolum {i}/{len(sections_plan)}: {len(out['narration'].split())} kelime")
        time.sleep(8)

    words = sum(len(s["narration"].split()) for s in sections)
    src_lines = "\n".join(f"- {s['title']} (Wikipedia): {s['url']}" for s in sources)
    description = (outline["description"].strip() +
                   "\n\nSources:\n" + src_lines +
                   "\n\n" + " ".join(outline["hashtags"]))
    result = {
        "generated": datetime.date.today().isoformat(),
        "topic": cand["working_title"],
        "title_options": outline["title_options"],
        "description": description,
        "hashtags": outline["hashtags"],
        "thumbnail_text": outline["thumbnail_text"],
        "total_words": words,
        "estimated_minutes": round(words / 150, 1),
        "sections": sections,
        "sources": [{"title": s["title"], "url": s["url"]} for s in sources],
    }
    os.makedirs("data", exist_ok=True)
    with open("data/script.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    md = [f"# {title}", "",
          "**Title options:** " + " | ".join(outline["title_options"]), "",
          f"**Thumbnail text:** {outline['thumbnail_text']}", "",
          f"**Words:** {words}  |  **Estimated length:** {result['estimated_minutes']} min", "",
          "## Description", "", description, ""]
    for i, s in enumerate(sections, 1):
        md += [f"## {i}. {s['heading']}", "", s["narration"], "",
               "*Visuals:* " + ", ".join(s["visual_keywords"]), ""]
    with open("data/script.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"Tamam: {words} kelime, yaklasik {result['estimated_minutes']} dakika.")


if __name__ == "__main__":
    main(*sys.argv[1:])
