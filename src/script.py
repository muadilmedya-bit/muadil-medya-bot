"""Secilen aday konu icin Wikipedia'dan kaynak toplar, senaryo yazar.
Kullanim: python src/script.py 4 config/profile_en.yaml
Cikti: data/script.json ve data/script.md
"""
import os, sys, json, time, re, datetime
import requests, yaml
from topics import call_gemini

API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "weekly-video-bot/0.2 (educational research; "
                    "https://github.com/muadilmedya-bit/muadil-medya-bot)"}
MAX_SOURCES = 10
MAX_CHARS = 6000
SECTIONS = 11
TARGET_WORDS = 320      # modele istenen kelime (model genelde daha kisa yazar)
MIN_WORDS = 230         # bunun altindaysa bolum genisletilir
SLEEP_BETWEEN = 10      # saniye, Gemini hiz siniri icin


def wiki_get(params, tries=5):
    """Wikipedia istegi; 429/5xx gelirse bekleyip tekrar dener."""
    for i in range(1, tries + 1):
        try:
            r = requests.get(API, headers=UA, params=params, timeout=30)
        except requests.RequestException as e:
            print(f"Wikipedia baglanti hatasi: {e}")
            time.sleep(3 * i)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            wait = int(r.headers.get("Retry-After", 5 * i))
            print(f"Wikipedia HTTP {r.status_code}, {wait} sn bekleniyor...")
            time.sleep(wait)
            continue
        print(f"Wikipedia HTTP {r.status_code}")
        return None
    return None


def wiki_extract(title):
    """Basliga gore makale metni dondurur: (gercek_baslik, metin) ya da None."""
    data = wiki_get({"action": "query", "prop": "extracts", "explaintext": 1,
                     "redirects": 1, "titles": title, "format": "json"})
    if not data:
        return None
    page = next(iter(data["query"]["pages"].values()))
    if "missing" in page or len(page.get("extract", "")) < 500:
        return None
    return page["title"], page["extract"]


def wiki_search_title(query):
    data = wiki_get({"action": "query", "list": "search", "srsearch": query,
                     "srlimit": 1, "format": "json"})
    if not data:
        return None
    hits = data["query"]["search"]
    return hits[0]["title"] if hits else None


def gather_sources(titles, queries):
    sources, seen, missing = [], set(), []

    def add(title):
        got = wiki_extract(title)
        time.sleep(1.5)
        if not got:
            missing.append(title)
            return
        real, text = got
        if real in seen or len(sources) >= MAX_SOURCES:
            return
        seen.add(real)
        sources.append({"title": real,
                        "url": "https://en.wikipedia.org/wiki/" + real.replace(" ", "_"),
                        "text": text[:MAX_CHARS]})

    for t in titles:
        if len(sources) >= MAX_SOURCES:
            break
        add(t)
    # Yeterli degilse arama ile tamamla
    for q in queries:
        if len(sources) >= 6:
            break
        t = wiki_search_title(q)
        time.sleep(1.5)
        if t:
            add(t)
    print("Bulunamayan basliklar:", missing)
    return sources


def ask(models, key, prompt, need=None, tries=3):
    for _ in range(tries):
        try:
            out = call_gemini(models, key, prompt)
            if need and not (isinstance(out, dict) and all(k in out for k in need)):
                raise ValueError(f"eksik anahtar: {need}")
            return out
        except (ValueError, KeyError, IndexError, TypeError) as e:
            print(f"Yanit gecersiz ({e}), tekrar deneniyor...")
            time.sleep(10)
    sys.exit("Gemini gecerli yanit vermedi.")


TITLES_PROMPT = """You are researching facts for a YouTube video.
Topic: {title}
Angle: {angle}

List {n} EXACT English Wikipedia article titles that contain solid factual material for this video.
Choose specific articles (for example a named technology, a named phenomenon, a named standard),
not broad ones like "Technology" or "Computer". Cover: the core subject, key people, important events
and dates, key technologies, turning points, and lasting consequences or practical lessons.
Return ONLY a JSON array of strings."""

OUTLINE_PROMPT = """You are the head writer of an English YouTube channel about {theme}.
Audience: {audience}. Video length: about {minutes} minutes.

Topic: {title}
Angle: {angle}

Plan a video of exactly {n} sections that tells a STORY, not an encyclopedia entry:
- section 1: open with one specific, vivid, relatable scenario, then pose the central question
- sections 2-{m}: move the story forward step by step (causes, key people and decisions, turning points,
  consequences), each section answering one question the viewer would naturally ask next
- section {n}: a closing that ties the story back to the central question and leaves one lasting
  takeaway (if the topic is practical, add a short spoken checklist), then a short sign-off

Rules: every section must serve the video's central question. Do NOT spend whole sections on
textbook definitions or unrelated background. Use ONLY facts found in the reference material.
All output in English.

Return ONLY a JSON object with these keys:
- central_question: the one question this whole video answers
- title_options: 3 honest, curiosity-driven titles under 70 characters, no false promises
- description: 2-3 sentence YouTube description
- hashtags: array of 5 hashtags (with #)
- thumbnail_text: at most 4 words
- sections: array of {n} objects, each with "heading", "purpose" (one sentence), "key_points"
  (3-5 concrete facts taken from the reference material: names, numbers, dates, mechanisms)

REFERENCE MATERIAL:
{material}
"""

SECTION_PROMPT = """You are writing the narration for one section of an English YouTube video about {theme}.
Video title: {title}
Central question of the whole video: {question}
Full outline: {outline}

Write section {i} of {n}: "{heading}"
Purpose: {purpose}
Key points to cover: {points}

STRICT RULES:
- Use ONLY facts found in the reference material below. Never invent a number, date, name or quote.
- If the material is thin on a point, say less rather than padding with vague claims.
- Write {words} words or more. Spoken English a narrator reads aloud: conversational, vivid, speaking to
  the viewer as "you". Use short sentences, concrete examples and everyday analogies (analogies must not
  state new facts).
- Do NOT write like an encyclopedia: no definition lists, no "X is a type of Y that..." openings.
- Do not copy sentence structure from the material; explain it in your own words.
- No markdown, bullet points, stage directions or URLs. No phrases like "in this section" or "according to".
- {style}

Return ONLY a JSON object with:
- narration: the text to be read aloud
- visuals: array of 8 objects in the order they should appear, each with:
    "type": one of "photo_historic" (old photo, engraving, map, drawing of a real person/place/object
            from the past), "film_archive" (old archive footage), "stock_modern" (modern stock footage,
            ONLY for present-day scenes or connections to today), "text_graphic" (a date, number or name
            shown on screen; then "query" is the exact text to show)
    "query": a short, concrete English search phrase (objects, people, places, events - never abstract ideas)
  Rules for visuals: anything that happened in the past must use photo_historic or film_archive, never
  stock_modern. Use real names and places in the query (for example "IBM 305 RAMAC 1956").

REFERENCE MATERIAL:
{material}
"""

EXPAND_PROMPT = """The narration below is too short. Rewrite it as a fuller version of at least {words} words.
Add concrete detail that appears in the reference material, a clear example or everyday analogy,
and smoother spoken transitions. Keep every fact consistent with the reference material and do not
invent numbers, dates or names. Same style: conversational English for a narrator, no markdown.

SECTION: {heading}
CURRENT NARRATION:
{narration}

Return ONLY a JSON object with: narration (the improved text).

REFERENCE MATERIAL:
{material}
"""


VISUAL_TYPES = {"photo_historic", "film_archive", "stock_modern", "text_graphic"}


def normalize_visuals(raw):
    out = []
    for v in raw if isinstance(raw, list) else []:
        if isinstance(v, dict) and v.get("query"):
            t = v.get("type") if v.get("type") in VISUAL_TYPES else "stock_modern"
            out.append({"type": t, "query": str(v["query"]).strip()})
        elif isinstance(v, str) and v.strip():
            out.append({"type": "stock_modern", "query": v.strip()})
    return out


def numeric_claims(sections):
    out = []
    for i, s in enumerate(sections, 1):
        for sent in re.split(r"(?<=[.!?])\s+", s["narration"]):
            if re.search(r"\d", sent):
                out.append((i, sent.strip()))
    return out


def main(index="1", profile_path="config/profile_en.yaml"):
    cfg = yaml.safe_load(open(profile_path, encoding="utf-8"))
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY tanimli degil")
    models = [cfg["gemini_model"]] + cfg.get(
        "fallback_models", ["gemini-3.1-flash-lite", "gemini-3.6-flash"])

    custom = os.environ.get("CUSTOM_TOPIC", "").strip()
    if custom:
        cand = {"working_title": custom,
                "angle": "A story-driven telling: the people, key decisions and turning points, "
                         "and why it still matters today.",
                "research_queries": [custom, custom + " history"]}
        theme = cfg.get("custom_theme", "the history of technology")
    else:
        data = json.load(open("data/candidates.json", encoding="utf-8"))
        cand = data["candidates"][int(index) - 1]
        theme = cfg["theme"]
    print("Secilen konu:", cand["working_title"])

    titles = ask(models, key, TITLES_PROMPT.format(
        title=cand["working_title"], angle=cand["angle"], n=14))
    if isinstance(titles, dict):
        titles = next(iter(titles.values()))
    print("Onerilen Wikipedia basliklari:", titles)

    sources = gather_sources(titles, cand["research_queries"])
    if len(sources) < 3:
        sys.exit("Yeterli kaynak bulunamadi, konuyu degistirin.")
    print(f"{len(sources)} kaynak toplandi:", [s["title"] for s in sources])
    material = "\n\n".join(f"### {s['title']}\n{s['text']}" for s in sources)

    outline = ask(models, key, OUTLINE_PROMPT.format(
        theme=theme, audience=cfg["audience"], minutes=cfg["video_minutes"],
        title=cand["working_title"], angle=cand["angle"],
        n=SECTIONS, m=SECTIONS - 1, material=material),
        need=["central_question", "title_options", "description",
              "hashtags", "thumbnail_text", "sections"])
    plan = outline["sections"]
    headings = [s["heading"] for s in plan]
    title = outline["title_options"][0]
    question = outline["central_question"]

    sections = []
    for i, sec in enumerate(plan, 1):
        if i == 1:
            style = ("Open with the vivid scenario in your first two sentences, "
                     "then pose the central question so the viewer must keep watching.")
        elif i == len(plan):
            style = ("End with a clear takeaway that ties back to the central question, then a short, "
                     "natural sign-off (if the topic is practical, include a brief spoken checklist).")
        else:
            style = ("Start by connecting to the previous section with one natural sentence, "
                     "then move the story forward without repeating it.")
        out = ask(models, key, SECTION_PROMPT.format(
            theme=theme, title=title, question=question,
            outline=json.dumps(headings), i=i, n=len(plan), heading=sec["heading"],
            purpose=sec.get("purpose", ""), points=json.dumps(sec["key_points"]),
            words=TARGET_WORDS, style=style, material=material),
            need=["narration", "visuals"])
        narration = out["narration"].strip()
        wc = len(narration.split())
        if wc < MIN_WORDS:
            print(f"Bolum {i}: {wc} kelime cok kisa, genisletiliyor...")
            time.sleep(SLEEP_BETWEEN)
            more = ask(models, key, EXPAND_PROMPT.format(
                words=TARGET_WORDS, heading=sec["heading"],
                narration=narration, material=material), need=["narration"])
            if len(more["narration"].split()) > wc:
                narration = more["narration"].strip()
        sections.append({"heading": sec["heading"], "narration": narration,
                         "visuals": normalize_visuals(out["visuals"])})
        print(f"Bolum {i}/{len(plan)}: {len(narration.split())} kelime")
        time.sleep(SLEEP_BETWEEN)

    words = sum(len(s["narration"].split()) for s in sections)
    src_lines = "\n".join(f"- {s['title']} (Wikipedia): {s['url']}" for s in sources)
    description = (outline["description"].strip() + "\n\nSources:\n" + src_lines +
                   "\n\n" + " ".join(outline["hashtags"]))
    claims = numeric_claims(sections)
    result = {
        "generated": datetime.date.today().isoformat(),
        "topic": cand["working_title"],
        "central_question": question,
        "title_options": outline["title_options"],
        "description": description,
        "hashtags": outline["hashtags"],
        "thumbnail_text": outline["thumbnail_text"],
        "total_words": words,
        "estimated_minutes": round(words / 150, 1),
        "sections": sections,
        "sources": [{"title": s["title"], "url": s["url"]} for s in sources],
        "facts_to_verify": [{"section": n, "sentence": t} for n, t in claims],
    }
    os.makedirs("data", exist_ok=True)
    with open("data/script.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    md = [f"# {title}", "",
          "**Central question:** " + question, "",
          "**Title options:** " + " | ".join(outline["title_options"]), "",
          f"**Thumbnail text:** {outline['thumbnail_text']}", "",
          f"**Words:** {words}  |  **Estimated length:** {result['estimated_minutes']} min", "",
          "## Description", "", description, ""]
    for i, s in enumerate(sections, 1):
        md += [f"## {i}. {s['heading']}", "", s["narration"], "",
               "*Visuals:* " + "; ".join(f"[{v['type']}] {v['query']}" for v in s["visuals"]), ""]
    md += ["## Facts to verify before publishing", ""]
    md += [f"- (Section {n}) {t}" for n, t in claims] or ["- none found"]
    with open("data/script.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"Tamam: {words} kelime, yaklasik {result['estimated_minutes']} dakika.")


if __name__ == "__main__":
    main(*sys.argv[1:])
