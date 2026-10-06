"""Secilen konu icin Wikipedia'dan kaynak toplar, senaryo yazar, dogrular.
Kullanim: python src/script.py 4 config/profile_en.yaml
Cikti: data/script.json ve data/script.md
"""
import os, sys, json, time, datetime
import requests, yaml
from llm import ask, set_budget, summary
from verify import verify_section, norm

API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "weekly-video-bot/0.3 (educational research; "
                    "https://github.com/muadilmedya-bit/muadil-medya-bot)"}
MAX_SOURCES = 10
MAX_CHARS = 8000
SECTIONS = 11
TARGET_WORDS = 340      # modele istenen kelime (model genelde daha kisa yazar)
MIN_WORDS = 240         # bunun altindaysa bolum genisletilir
SLEEP_BETWEEN = 8       # saniye, Gemini hiz siniri icin


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
    for q in queries:  # yeterli degilse arama ile tamamla
        if len(sources) >= 6:
            break
        t = wiki_search_title(q)
        time.sleep(1.5)
        if t:
            add(t)
    print("Bulunamayan basliklar:", missing)
    return sources


TITLES_PROMPT = """You are researching facts for a YouTube video.
Topic: {title}
Angle: {angle}

List {n} EXACT English Wikipedia article titles that contain solid factual material for this video.
Choose specific articles (a named technology, person, event, organization or standard),
not broad ones like "Technology" or "Computer". Cover: the core subject, key people, important
events and dates, key technologies, turning points, and lasting consequences.
IMPORTANT: the articles together must cover the story from its beginning all the way to the end point
that the topic promises (for example, include the later developments and the modern outcome, not only
the origins). Include at least 3 articles about the later part of the story.
Return ONLY a JSON array of strings."""

OUTLINE_PROMPT = """You are the head writer of an English YouTube channel about {theme}.
Audience: {audience}. Video length: about {minutes} minutes.

Topic: {title}
Angle: {angle}

Plan a video of exactly {n} sections that tells a STORY, not an encyclopedia entry:
- section 1: open with one specific, vivid, relatable scene, then pose the central question
- sections 2-{m}: move the story forward step by step (causes, key people and decisions, turning points,
  consequences), each section answering one question the viewer would naturally ask next
- section {n}: a closing that ties the story back to the central question and leaves one lasting
  takeaway, then a short sign-off

Rules:
- Every section must serve the central question, and the sections together must reach the end point the
  question promises (do not stop halfway through the story).
- If a popular myth surrounds this topic, find out from the reference material what is actually true and
  address the myth honestly in the video. Never present a myth as fact, not even in the title.
- Each anecdote, example, scene or person introduction may appear in the key_points of ONE section only.
- No whole sections of textbook definitions or unrelated background.
- Use ONLY facts found in the reference material. All output in English.

Return ONLY a JSON object with these keys:
- central_question: the one question this whole video answers
- title_options: 3 honest, curiosity-driven titles under 70 characters, no false promises
- description: 2-3 sentence YouTube description
- hashtags: array of 5 hashtags (with #)
- thumbnail_text: at most 4 words
- sections: array of {n} objects, each with "heading", "purpose" (one sentence), "key_points"
  (3-5 concrete facts from the reference material: names, dates, numbers, mechanisms)

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

SCRIPT SO FAR (earlier sections, already told; never repeat their scenes, anecdotes, examples or analogies):
{so_far}

STRICT RULES:
- Use ONLY facts found in the reference material. Never invent a number, date, name or quote.
- Be careful with dates: say exactly what the reference material says happened on that date, and keep the
  order of events as the material gives it.
- Avoid absolute claims (never, always, no one, impossible) unless the reference material says so.
- If the material is thin on a point, say less rather than padding with vague claims.
- Write {words} words or more. Spoken English a narrator reads aloud: conversational and vivid, speaking
  to the viewer as "you", short sentences.
- At most 3 numbers or dates in the whole section, only those that matter to the story.
- At most ONE analogy in this section, and do not reuse an analogy theme that appears in the script so far
  (for example railways, highways, mail or postal service, shipping, libraries).
- Do not use the phrases "Think of it like", "Imagine" or "Picture". Do not start the section with "As",
  "While" or "Building on". Vary your sentence openings.
- Do NOT write like an encyclopedia: no definition lists, no "X is a type of Y that..." openings.
- Explain in your own words; do not copy sentence structure from the material.
- No markdown, bullet points, stage directions or URLs. No phrases like "in this section" or "according to".
- {style}

Return ONLY a JSON object with:
- narration: the text to be read aloud
- visuals: array of 8 objects in the order they should appear, each with:
    "type": one of "photo_historic" (old photo, engraving, map, drawing of a real person/place/object
            from the past), "film_archive" (old archive footage), "stock_modern" (modern stock footage,
            ONLY for present-day scenes or connections to today), "text_graphic" (a date, number or name
            shown on screen; then "query" is the exact text to show, at most 6 words)
    "query": a short, concrete English search phrase (objects, people, places, events - never abstract ideas)
  Anything that happened in the past must use photo_historic or film_archive, never stock_modern.
  Use real names and places in the query (for example "IBM 305 RAMAC 1956").

REFERENCE MATERIAL:
{material}
"""

EXPAND_PROMPT = """The narration below is too short. Rewrite it as a fuller version of at least {words} words.
Add concrete detail that appears in the reference material, one clear example, and smoother spoken
transitions. Keep every fact consistent with the reference material and do not invent numbers, dates or
names. Do not repeat anything told in the earlier sections. Same style: conversational English for a
narrator, no markdown, no "Think of it like", "Imagine" or "Picture".

SECTION: {heading}
SCRIPT SO FAR:
{so_far}

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


def txt(x):
    """Gemini bazen duz metin yerine {'title': ...} gibi kutular dondurur; metne cevirir."""
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, dict):
        for k in ("title", "text", "name", "option", "value", "hashtag", "description"):
            if isinstance(x.get(k), str):
                return x[k].strip()
        for v in x.values():
            if isinstance(v, str):
                return v.strip()
        return json.dumps(x, ensure_ascii=False)
    if isinstance(x, list):
        return " ".join(txt(i) for i in x)
    return str(x).strip()


def as_list(x):
    return x if isinstance(x, list) else [x]


def clean_outline(o):
    o["central_question"] = txt(o["central_question"])
    o["description"] = txt(o["description"])
    o["thumbnail_text"] = txt(o["thumbnail_text"])
    o["title_options"] = [t for t in (txt(t) for t in as_list(o["title_options"])) if t] or ["Untitled"]
    tags = []
    for h in as_list(o["hashtags"]):
        h = txt(h).replace(" ", "")
        if h:
            tags.append(h if h.startswith("#") else "#" + h)
    o["hashtags"] = tags[:5]
    for sec in o["sections"]:
        sec["heading"] = txt(sec.get("heading", ""))
        sec["purpose"] = txt(sec.get("purpose", ""))
        sec["key_points"] = [txt(k) for k in as_list(sec.get("key_points", []))]
    return o


def main(index="1", profile_path="config/profile_en.yaml"):
    cfg = yaml.safe_load(open(profile_path, encoding="utf-8"))
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY tanimli degil")
    set_budget(cfg.get("max_calls_per_run", 70))
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
        title=cand["working_title"], angle=cand["angle"], n=16))
    if isinstance(titles, dict):
        titles = next(iter(titles.values()))
    print("Onerilen Wikipedia basliklari:", titles)

    sources = gather_sources(titles, cand["research_queries"])
    if len(sources) < 3:
        sys.exit("Yeterli kaynak bulunamadi, konuyu degistirin.")
    print(f"{len(sources)} kaynak toplandi:", [s["title"] for s in sources])
    material = "\n\n".join(f"### {s['title']}\n{s['text']}" for s in sources)
    material_norm = norm(material)

    outline = ask(models, key, OUTLINE_PROMPT.format(
        theme=theme, audience=cfg["audience"], minutes=cfg["video_minutes"],
        title=cand["working_title"], angle=cand["angle"],
        n=SECTIONS, m=SECTIONS - 1, material=material),
        need=["central_question", "title_options", "description",
              "hashtags", "thumbnail_text", "sections"])
    outline = clean_outline(outline)
    plan = outline["sections"]
    headings = [s["heading"] for s in plan]
    title = outline["title_options"][0]
    question = outline["central_question"]
    print("Merkezi soru:", question)

    sections, told, all_results = [], [], []
    for i, sec in enumerate(plan, 1):
        if i == 1:
            style = ("Open with the vivid scene in your first two sentences, "
                     "then pose the central question so the viewer must keep watching.")
        elif i == len(plan):
            style = ("End with a clear takeaway that ties back to the central question, then a short, "
                     "natural sign-off. Do not ask for likes or subscriptions.")
        else:
            style = ("Start with one natural sentence that connects to the previous section, "
                     "then move the story forward without repeating it.")
        so_far = "\n\n".join(f"[{j}] {t}" for j, t in enumerate(told, 1)) or \
                 "(nothing yet, this is the first section)"
        out = ask(models, key, SECTION_PROMPT.format(
            theme=theme, title=title, question=question,
            outline=json.dumps(headings), i=i, n=len(plan), heading=sec["heading"],
            purpose=sec.get("purpose", ""), points=json.dumps(sec["key_points"]),
            so_far=so_far, words=TARGET_WORDS, style=style, material=material),
            need=["narration", "visuals"])
        narration = out["narration"].strip()
        wc = len(narration.split())
        if wc < MIN_WORDS:
            print(f"Bolum {i}: {wc} kelime cok kisa, genisletiliyor...")
            time.sleep(SLEEP_BETWEEN)
            more = ask(models, key, EXPAND_PROMPT.format(
                words=TARGET_WORDS, heading=sec["heading"], so_far=so_far,
                narration=narration, material=material), need=["narration"])
            if len(more["narration"].split()) > wc:
                narration = more["narration"].strip()
        time.sleep(SLEEP_BETWEEN)

        narration, results = verify_section(models, key, narration, material, material_norm)
        for r in results:
            r["section"] = i
        all_results += results
        bad = [r for r in results if r["status"] != "supported"]
        print(f"Bolum {i}/{len(plan)}: {len(narration.split())} kelime, "
              f"{len(results)} iddia, {len(bad)} supheli")
        sections.append({"heading": sec["heading"], "narration": narration,
                         "visuals": normalize_visuals(out["visuals"])})
        told.append(narration)
        time.sleep(SLEEP_BETWEEN)

    words = sum(len(s["narration"].split()) for s in sections)
    counts = {}
    for r in all_results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    flagged = [r for r in all_results if r["status"] != "supported" or r["auto_fixed"]]
    src_lines = "\n".join(f"- {s['title']} (Wikipedia): {s['url']}" for s in sources)
    description = (outline["description"].strip() + "\n\nSources:\n" + src_lines +
                   "\n\n" + " ".join(outline["hashtags"]))
    usage = summary()
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
        "verification": {"counts": counts, "flagged": flagged},
        "gemini_usage": usage,
    }
    os.makedirs("data", exist_ok=True)
    with open("data/script.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    md = [f"# {title}", "",
          "**Central question:** " + question, "",
          "**Title options:** " + " | ".join(outline["title_options"]), "",
          f"**Thumbnail text:** {outline['thumbnail_text']}", "",
          f"**Words:** {words}  |  **Estimated length:** {result['estimated_minutes']} min  |  "
          f"**Gemini calls:** {usage['calls']}", "",
          "## Description", "", description, ""]
    for i, s in enumerate(sections, 1):
        md += [f"## {i}. {s['heading']}", "", s["narration"], "",
               "*Visuals:* " + "; ".join(f"[{v['type']}] {v['query']}" for v in s["visuals"]), ""]
    md += ["## Verification report", "",
           f"Checked {len(all_results)} claims against the sources: " +
           ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())), ""]
    if not flagged:
        md += ["No problems found.", ""]
    for r in flagged:
        line = f"- (Section {r['section']}) [{r['status']}{', auto-fixed' if r['auto_fixed'] else ''}] {r['claim']}"
        if r["fix"]:
            line += f"  -> suggested: {r['fix']}"
        if r["evidence"]:
            line += f"  (source says: \"{r['evidence']}\")"
        md.append(line)
    with open("data/script.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"Tamam: {words} kelime, yaklasik {result['estimated_minutes']} dakika, "
          f"{usage['calls']} Gemini cagrisi, dogrulama: {counts}")


if __name__ == "__main__":
    main(*sys.argv[1:])
