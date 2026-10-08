"""Gorsel bulma: data/script.json -> out/visuals/visuals.json + preview.html + credits.txt
Kaynaklar: Wikimedia Commons (tarihi fotograflar), Pexels (modern stok video ve fotograf).
Kullanim: python src/visuals.py config/profile_en.yaml
Ortam degiskenleri: LIMIT (kac bolum, 0 = hepsi), GEMINI_API_KEY (gorsel editoru icin), PEXELS_API_KEY ve/veya PIXABAY_API_KEY (istege bagli)
Bu asamada dosya INDIRILMEZ: sadece secilen gorsellerin adresleri, lisanslari ve onizleme sayfasi uretilir.
Indirme ve montaj sonraki asamada (render) yapilir."""
import os, sys, re, json, time, html, datetime
import requests, yaml
from llm import ask, set_budget, summary

UA = {"User-Agent": "weekly-video-bot/0.4 (educational video research; "
                    "https://github.com/muadilmedya-bit/muadil-medya-bot)"}
COMMONS = "https://commons.wikimedia.org/w/api.php"
PEXELS_VIDEO = "https://api.pexels.com/videos/search"
PEXELS_PHOTO = "https://api.pexels.com/v1/search"
PIXABAY_PHOTO = "https://pixabay.com/api/"
PIXABAY_VIDEO = "https://pixabay.com/api/videos/"
PER_QUERY_PHOTO = 3
JUDGE_CANDS = 6        # editore (yapay zekaya) sunulacak en fazla aday sayisi
PER_QUERY_VIDEO = 2
PEXELS_CREDIT = "Stock video and photos provided by Pexels: https://www.pexels.com"


def get_json(url, params, headers, tries=4):
    for i in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
        except requests.RequestException as e:
            print(f"  baglanti hatasi: {e}")
            time.sleep(3 * i)
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        if r.status_code in (429, 500, 502, 503, 504):
            wait = int(r.headers.get("Retry-After", 5 * i))
            print(f"  HTTP {r.status_code}, {wait} sn bekleniyor...")
            time.sleep(wait)
            continue
        print(f"  HTTP {r.status_code} ({url.split('/')[2]})")
        return None
    return None


# ------------------------------------------------------------------ Wikimedia Commons
GENERIC = re.compile(r"^(?:(?:19|20)\d0s|\d{4}|photo|photos|photograph|portrait|picture|image|archive|"
                     r"footage|vintage|old|historic|historical)$", re.I)
DENY = re.compile(r"\bnc\b|\bnd\b|non-?commercial|no[- ]?deriv|fair use|copyrighted|all rights reserved", re.I)
ALLOW = re.compile(r"public domain|\bpd\b|\bpdm\b|cc0|cc[- ]by|u\.?s\.? government|usgov|no known|attribution", re.I)
PD = re.compile(r"public domain|\bpd\b|\bpdm\b|cc0|u\.?s\.? government|usgov", re.I)


def query_variants(q):
    words = q.split()
    core = [w for w in words if not GENERIC.match(w)]
    out = [q]
    if core and core != words:
        out.append(" ".join(core))
    if len(core) > 3:
        out.append(" ".join(core[:3]))
    seen, res = set(), []
    for v in out:
        if v and v.lower() not in seen:
            seen.add(v.lower())
            res.append(v)
    return res


STOP = {"the", "and", "for", "with", "from", "that", "this", "view", "scene", "building", "room", "office",
        "computer", "computers", "machine", "equipment", "lab", "laboratory", "hardware", "system", "network",
        "terminal", "early", "first", "modern", "close", "campus", "headquarters", "interior", "exterior", "aerial"}


def distinctive(q):
    out = []
    for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/]*", q):
        if len(t) < 3 or t.lower() in STOP or GENERIC.match(t):
            continue
        out.append(t)
    return out


def _flat(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "


def relevance(q, text, strict):
    """Sorgudaki ayirt edici kelimeler dosya adi/aciklama/kategoride geciyor mu?
    strict: kisaltmalar (UCLA, ARPA) ve model numaralari (Q-32) zorunlu, kelimelerin %60'i gerekli."""
    toks = distinctive(q)
    if not toks:
        return True
    t = _flat(text)
    has = lambda tok: _flat(tok) in t
    score = sum(1 for tok in toks if has(tok)) / len(toks)
    if strict:
        must = [tok for tok in toks if (tok.isupper() and len(tok) >= 3) or
                (re.search(r"\d", tok) and not re.fullmatch(r"\d{4}", tok))]
        if any(not has(m) for m in must):
            return False
        return score >= 0.6
    return score >= 0.5


def query_era(q):
    ys = [int(y) for y in re.findall(r"\b(1[5-9]\d\d|20[0-3]\d)\b", q)]
    for d in re.findall(r"\b(1[5-9]\d0|20[0-3]0)s\b", q):
        ys += [int(d), int(d) + 9]
    return (min(ys), max(ys)) if ys else None


def era_ok(q, year):
    """Sorgu bir donem belirtiyorsa, cok daha sonraki tarihli (modern) fotograflari ele."""
    era = query_era(q)
    return not (era and year and year > era[1] + 25)


def license_ok(lic, allow_sharealike=True):
    if not lic or DENY.search(lic):
        return False
    if not allow_sharealike and re.search(r"-sa\b|share[- ]?alike", lic, re.I):
        return False
    return bool(ALLOW.search(lic))


def strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_commons_page(pg, allow_sa):
    ii = (pg.get("imageinfo") or [None])[0]
    if not ii or ii.get("mime") not in ("image/jpeg", "image/png"):
        return None
    w, h = ii.get("width", 0), ii.get("height", 0)
    if max(w, h) < 900 or min(w, h) < 500:
        return None
    md = ii.get("extmetadata") or {}
    lic = (md.get("LicenseShortName") or {}).get("value", "")
    if not license_ok(lic, allow_sa):
        return None
    artist = strip_html((md.get("Artist") or {}).get("value", "")) or \
        strip_html((md.get("Credit") or {}).get("value", ""))
    title = pg.get("title", "").replace("File:", "")
    page_url = ii.get("descriptionurl", "")
    text = " ".join([title.rsplit(".", 1)[0], strip_html((md.get("ImageDescription") or {}).get("value", "")),
                     strip_html((md.get("ObjectName") or {}).get("value", "")),
                     strip_html((md.get("Categories") or {}).get("value", "")).replace("|", " ")])
    date_raw = strip_html((md.get("DateTimeOriginal") or {}).get("value", "") or
                          (md.get("DateTime") or {}).get("value", ""))
    ym = re.search(r"\b(1[5-9]\d\d|20[0-4]\d)\b", date_raw)
    needs = not PD.search(lic)
    attribution = f'"{title}"' + (f" by {artist[:100]}" if artist and needs else "") + \
                  f" ({lic}), Wikimedia Commons: {page_url}"
    return {"kind": "image", "source": "commons", "url": ii.get("thumburl") or ii.get("url"),
            "original_url": ii.get("url"), "page_url": page_url,
            "width": ii.get("thumbwidth") or w, "height": ii.get("thumbheight") or h,
            "title": title, "author": artist[:120], "license": lic,
            "attribution_required": needs, "attribution": attribution, "_text": text,
            "year": int(ym.group(1)) if ym else None,
            "desc": strip_html((md.get("ImageDescription") or {}).get("value", ""))[:240],
            "cats": strip_html((md.get("Categories") or {}).get("value", "")).replace("|", "; ")[:200]}


def commons_search(q, allow_sa, want):
    seen, cands = set(), []
    for variant in query_variants(q):
        for suffix in (" filetype:bitmap", ""):
            data = get_json(COMMONS, {
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": variant + suffix, "gsrnamespace": 6, "gsrlimit": 20,
                "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1920,
                "iiextmetadatafilter": "LicenseShortName|Artist|Credit|ImageDescription|ObjectName|Categories|DateTimeOriginal|DateTime"}, UA)
            time.sleep(0.4)
            pages = ((data or {}).get("query") or {}).get("pages") or {}
            for pg in sorted(pages.values(), key=lambda p: p.get("index", 999)):
                c = parse_commons_page(pg, allow_sa)
                if c and era_ok(q, c.get("year")) and relevance(q, c["_text"], strict=True) \
                        and c["title"] not in seen:
                    seen.add(c["title"])
                    cands.append(c)
            if pages:
                break  # filtreli arama sonuc verdiyse filtresizini deneme
        if len(cands) >= want:
            break
    cands.sort(key=lambda c: 0 if max(c["width"], c["height"]) >= 1280 else 1)
    return cands


WIKI = "https://en.wikipedia.org/w/api.php"


def build_pool(source_titles, allow_sa):
    """Senaryonun kaynak makalelerinde kullanilan, Commons'ta lisansi uygun gorselleri toplar."""
    files = {}
    for t in source_titles:
        data = get_json(WIKI, {"action": "query", "format": "json", "titles": t, "prop": "images",
                               "imlimit": "max"}, UA)
        time.sleep(0.3)
        for pg in (((data or {}).get("query") or {}).get("pages") or {}).values():
            for im in pg.get("images", []):
                n = im.get("title", "")
                if n and re.search(r"\.(jpe?g|png)$", n, re.I):
                    files.setdefault(n, t)
    names = list(files)
    pool, seen = [], set()
    for i in range(0, len(names), 40):
        data = get_json(COMMONS, {
            "action": "query", "format": "json", "titles": "|".join(names[i:i + 40]),
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1920,
            "iiextmetadatafilter": "LicenseShortName|Artist|Credit|ImageDescription|ObjectName|Categories|DateTimeOriginal|DateTime"}, UA)
        time.sleep(0.4)
        for pg in (((data or {}).get("query") or {}).get("pages") or {}).values():
            c = parse_commons_page(pg, allow_sa)
            if c and c["title"] not in seen:
                seen.add(c["title"])
                c["from_source_article"] = True
                c["article"] = files.get(pg.get("title"), "")
                pool.append(c)
    print(f"Kaynak makalelerde {len(names)} gorsel, {len(pool)} tanesi Commons'ta ve lisansi uygun")
    return pool


def rank_pool(q, pool):
    scored = []
    for c in pool:
        if era_ok(q, c.get("year")) and relevance(q, c["_text"], strict=False):
            toks = distinctive(q)
            sc = sum(1 for t in toks if _flat(t) in _flat(c["_text"])) / max(len(toks), 1)
            scored.append((sc, max(c["width"], c["height"]), c))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [c for _, _, c in scored]


# ------------------------------------------------------------------ Pexels
def pexels_videos(q, key):
    data = get_json(PEXELS_VIDEO, {"query": q, "per_page": 15, "orientation": "landscape", "size": "medium"},
                    {"Authorization": key})
    out = []
    for v in (data or {}).get("videos", []):
        if (v.get("duration") or 0) < 5:
            continue
        files = [f for f in v.get("video_files", [])
                 if f.get("file_type") == "video/mp4" and f.get("width") and 1280 <= f["width"] <= 1920]
        if not files:
            continue
        f = min(files, key=lambda f: f["width"])
        author = (v.get("user") or {}).get("name", "")
        out.append({"kind": "video", "source": "pexels", "url": f["link"], "thumb": v.get("image", ""),
                    "page_url": v.get("url", ""), "width": f["width"], "height": f.get("height"),
                    "duration": v.get("duration"), "title": f"Pexels video {v.get('id')}",
                    "author": author, "license": "Pexels License", "attribution_required": False,
                    "attribution": f"Video by {author} on Pexels: {v.get('url', '')}"})
    return out


def pexels_photos(q, key):
    data = get_json(PEXELS_PHOTO, {"query": q, "per_page": 15, "orientation": "landscape"},
                    {"Authorization": key})
    out = []
    for p in (data or {}).get("photos", []):
        src = p.get("src") or {}
        if not src.get("large2x"):
            continue
        out.append({"kind": "image", "source": "pexels", "url": src["large2x"], "thumb": src.get("medium", ""),
                    "page_url": p.get("url", ""), "width": p.get("width"), "height": p.get("height"),
                    "title": f"Pexels photo {p.get('id')}", "author": p.get("photographer", ""),
                    "license": "Pexels License", "attribution_required": False,
                    "attribution": f"Photo by {p.get('photographer', '')} on Pexels: {p.get('url', '')}"})
    return out


# ------------------------------------------------------------------ Pixabay
def pixabay_videos(q, key):
    data = get_json(PIXABAY_VIDEO, {"key": key, "q": q, "per_page": 15, "safesearch": "true"}, UA)
    out = []
    for v in (data or {}).get("hits", []):
        if (v.get("duration") or 0) < 5:
            continue
        files = v.get("videos") or {}
        f = next((files[k] for k in ("large", "medium") if files.get(k, {}).get("url")
                  and (files[k].get("width") or 0) >= 1280), None)
        if not f:
            continue
        author = v.get("user", "")
        out.append({"kind": "video", "source": "pixabay", "url": f["url"], "thumb": f.get("thumbnail", ""),
                    "page_url": v.get("pageURL", ""), "width": f.get("width"), "height": f.get("height"),
                    "duration": v.get("duration"), "title": f"Pixabay video {v.get('id')}",
                    "author": author, "license": "Pixabay Content License", "attribution_required": False,
                    "attribution": f"Video by {author} on Pixabay: {v.get('pageURL', '')}"})
    return out


def pixabay_photos(q, key):
    data = get_json(PIXABAY_PHOTO, {"key": key, "q": q, "image_type": "photo", "orientation": "horizontal",
                                    "min_width": 1280, "per_page": 15, "safesearch": "true"}, UA)
    out = []
    for p in (data or {}).get("hits", []):
        if not p.get("largeImageURL"):
            continue
        author = p.get("user", "")
        out.append({"kind": "image", "source": "pixabay", "url": p["largeImageURL"],
                    "thumb": p.get("webformatURL", ""), "page_url": p.get("pageURL", ""),
                    "width": p.get("imageWidth"), "height": p.get("imageHeight"),
                    "title": f"Pixabay photo {p.get('id')}", "author": author,
                    "license": "Pixabay Content License", "attribution_required": False,
                    "attribution": f"Photo by {author} on Pixabay: {p.get('pageURL', '')}"})
    return out


# ------------------------------------------------------------------ secim
def find_stock(query, keys, used):
    """Modern stok video/foto. Anahtar yoksa ya da bulunamazsa yazi karti."""
    if not (keys.get("pexels") or keys.get("pixabay")):
        return [{"kind": "text", "text": query, "fallback": True}], True, "no stock API key"
    cands = []
    if keys.get("pexels"):
        cands += pexels_videos(query, keys["pexels"])
    if len(cands) < PER_QUERY_VIDEO and keys.get("pixabay"):
        cands += pixabay_videos(query, keys["pixabay"])
    if len(cands) < PER_QUERY_VIDEO and keys.get("pexels"):
        cands += pexels_photos(query, keys["pexels"])
    if len(cands) < PER_QUERY_VIDEO and keys.get("pixabay"):
        cands += pixabay_photos(query, keys["pixabay"])
    cands = [c for c in cands if c["url"] not in used][:PER_QUERY_VIDEO]
    if not cands:
        return [{"kind": "text", "text": query, "fallback": True}], True, "nothing found"
    for c in cands:
        used.add(c["url"])
    return cands, False, ""


def historic_candidates(query, allow_sa, pool):
    """Tarihi fotograf adaylari: once kaynak makalelerin gorselleri, sonra Commons aramasi."""
    cands = rank_pool(query, pool)[:4]
    if len(cands) < 4:
        have = {c["url"] for c in cands}
        cands += [c for c in commons_search(query, allow_sa, JUDGE_CANDS - len(cands)) if c["url"] not in have]
    return cands[:JUDGE_CANDS]


JUDGE_PROMPT = """You are the visual editor of a documentary YouTube channel about {theme}.
Video topic: {topic}
Section {i}: "{heading}". The narrator says:
{narration}

Below are image requests for this section. For every request, candidate images are listed with METADATA ONLY
(file title, year, the Wikipedia article they were taken from, description, categories).

Accept a candidate only if its metadata clearly shows that it depicts the requested subject in a way that
fits this section:
- Right person: not a namesake (an athlete, actor, painter or relative with the same name is NOT acceptable).
- Right place or object, and a plausible era. If the request names a period, a modern photo is NOT acceptable.
- The image must illustrate the story being told. Photos of protests, attacks, disasters or other unrelated
  events at the same place are NOT acceptable, even if they show the same building.
- An image taken from the Wikipedia article about the requested subject is a strong positive sign.
When in doubt, reject. Rejecting every candidate is fine: a text card will be shown instead.

Return ONLY a JSON object: {{"decisions": [{{"id": <request number>, "accept": [<candidate numbers, best first>],
"reason": "<one short sentence>"}}]}}

{requests}
"""


def judge_section(models, key, theme, topic, i, heading, narration, pending):
    lines = []
    for n, it in enumerate(pending, 1):
        lines.append(f'REQUEST {n}: type={it["type"]}  query="{it["query"]}"')
        for j, c in enumerate(it["_cands"], 1):
            lines.append(f'  [{j}] title: {c["title"]} | year: {c.get("year") or "unknown"} | '
                         f'article: {c.get("article") or "-"} | description: {c.get("desc", "")[:200]} | '
                         f'categories: {c.get("cats", "")[:150]}')
    out = ask(models, key, JUDGE_PROMPT.format(theme=theme, topic=topic, i=i, heading=heading,
                                               narration=narration[:1800], requests="\n".join(lines)),
              need=["decisions"], temperature=0.1)
    res = {}
    for d in out["decisions"]:
        try:
            n = int(d["id"])
            acc = [int(x) for x in d.get("accept", [])]
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        res[n] = (acc, str(d.get("reason", ""))[:160])
    return res


def preview_html(data):
    esc = html.escape
    rows = []
    st = data["stats"]
    rows.append("<table><tr><th>Type</th><th>Requests</th><th>Found</th><th>Fallback (text card)</th></tr>")
    for t, s in sorted(st.items()):
        rows.append(f"<tr><td>{esc(t)}</td><td>{s['requests']}</td><td>{s['found']}</td><td>{s['fallback']}</td></tr>")
    rows.append("</table>")
    for sec in data["sections"]:
        rows.append(f"<h2>{sec['index']}. {esc(sec['heading'])}</h2>")
        for it in sec["items"]:
            cls = "fb" if it["fallback"] else ""
            rows.append(f"<div class='item {cls}'><div class='meta'><b>{esc(it['type'])}</b> &mdash; "
                        f"{esc(it['query'])} {('<i>(' + esc(it['note']) + ')</i>') if it['note'] else ''}</div><div class='row'>")
            for c in it["choices"]:
                if c["kind"] == "text":
                    rows.append(f"<div class='card text'>{esc(c['text'])}</div>")
                else:
                    thumb = c.get("thumb") or c["url"]
                    cap = f"{esc(c['source'])} &middot; {esc(c['license'])} &middot; {c.get('width')}x{c.get('height')}"
                    if c["kind"] == "video":
                        cap += f" &middot; video {c.get('duration')}s"
                    rows.append(f"<div class='card'><a href='{esc(c['page_url'])}' target='_blank'>"
                                f"<img src='{esc(thumb)}' loading='lazy'></a><div class='cap'>{cap}<br>"
                                f"{esc(c['title'][:70])}</div></div>")
            rows.append("</div></div>")
    css = ("body{font-family:sans-serif;background:#111;color:#ddd;margin:20px}h2{border-bottom:1px solid #444;"
           "padding-bottom:4px;margin-top:30px}table{border-collapse:collapse}td,th{border:1px solid #444;padding:4px 10px}"
           ".item{margin:10px 0;padding:8px;border-left:3px solid #4a8}.item.fb{border-left-color:#c84}"
           ".row{display:flex;gap:10px;flex-wrap:wrap}.card{width:260px}.card img{width:260px;height:150px;"
           "object-fit:cover;background:#222}.cap{font-size:11px;color:#999}.text{background:#333;padding:20px;"
           "min-height:60px;display:flex;align-items:center}a{color:#8cf}")
    return f"<!doctype html><meta charset='utf-8'><title>Visuals preview</title><style>{css}</style>" \
           f"<h1>Visuals preview: {esc(data['topic'])}</h1>" + "".join(rows)


def main(profile_path="config/profile_en.yaml"):
    cfg = yaml.safe_load(open(profile_path, encoding="utf-8"))
    vis_cfg = cfg.get("visuals", {}) or {}
    allow_sa = vis_cfg.get("allow_sharealike", True)
    keys = {"pexels": os.environ.get("PEXELS_API_KEY", "").strip(),
            "pixabay": os.environ.get("PIXABAY_API_KEY", "").strip()}
    gem_key = os.environ.get("GEMINI_API_KEY", "").strip()
    set_budget(vis_cfg.get("max_calls", 40))
    models = [cfg["gemini_model"]] + cfg.get("fallback_models", ["gemini-3.1-flash-lite", "gemini-3.6-flash"])
    judge_models = [cfg.get("writer_model", "gemini-3.5-flash")] + models
    theme = cfg.get("custom_theme", "the history of technology")
    script = json.load(open("data/script.json", encoding="utf-8"))
    limit = int(os.environ.get("LIMIT", "0") or 0)
    secs = script["sections"][:limit] if limit else script["sections"]
    print("Stok anahtarlari:", {k: ("var" if v else "yok") for k, v in keys.items()},
          "| gorsel editoru (Gemini):", "var" if gem_key else "YOK (sadece kural tabanli filtre)")

    pool = []
    if any(v["type"] in ("photo_historic", "film_archive") for sec in secs for v in sec.get("visuals", [])):
        pool = build_pool([src["title"] for src in script.get("sources", [])][:12], allow_sa)
    used, stats, out_secs = set(), {}, []
    for si, sec in enumerate(secs, 1):
        items, pending = [], []
        for v in sec.get("visuals", []):
            vtype, q = v["type"], v["query"]
            it = {"type": vtype, "query": q, "fallback": False, "note": "", "choices": []}
            if vtype == "text_graphic":
                it["choices"] = [{"kind": "text", "text": q}]
            elif vtype == "stock_modern":
                it["choices"], it["fallback"], it["note"] = find_stock(q, keys, used)
            else:
                if vtype == "film_archive":
                    it["note"] = "archive footage not available automatically; using stills"
                it["_cands"] = historic_candidates(q, allow_sa, pool)
                pending.append(it)
            items.append(it)

        decisions = {}
        with_cands = [it for it in pending if it["_cands"]]
        if with_cands and gem_key:
            try:
                decisions = judge_section(judge_models, gem_key, theme, script.get("topic", ""), si,
                                          sec["heading"], sec.get("narration", ""), with_cands)
            except SystemExit as e:
                print(f"  Gorsel editoru calismadi ({e}); kural tabanli filtre kullaniliyor")
        for n, it in enumerate(with_cands, 1):
            cands = it.pop("_cands")
            if gem_key and decisions:
                acc, reason = decisions.get(n, ([], "no decision"))
                picked = [cands[k - 1] for k in acc if 1 <= k <= len(cands)]
                it["note"] = (it["note"] + "; " if it["note"] else "") + f"editor kept {len(picked)}/{len(cands)}: {reason}"
            else:
                picked = cands
                it["note"] = (it["note"] + "; " if it["note"] else "") + "unjudged"
            picked = [c for c in picked if c["url"] not in used][:PER_QUERY_PHOTO]
            picked = [{k: v for k, v in c.items() if k != "_text"} for c in picked]
            for c in picked:
                used.add(c["url"])
            it["choices"] = picked
        for it in pending:
            it.pop("_cands", None)
            if not it["choices"]:
                it["choices"] = [{"kind": "text", "text": it["query"], "fallback": True}]
                it["fallback"] = True
        for it in items:
            s_ = stats.setdefault(it["type"], {"requests": 0, "found": 0, "fallback": 0})
            s_["requests"] += 1
            s_["fallback" if it["fallback"] else "found"] += 1
        out_secs.append({"index": si, "heading": sec["heading"], "items": items})
        nf = sum(1 for i in items if i["fallback"])
        print(f"Bolum {si}/{len(secs)}: {len(items)} istek, {len(items) - nf} bulundu, {nf} yazi karti")

    data = {"generated": datetime.date.today().isoformat(), "topic": script.get("topic", ""),
            "pexels_credit": PEXELS_CREDIT, "stats": stats, "gemini_usage": summary(), "sections": out_secs}
    out = "out/visuals"
    os.makedirs(out, exist_ok=True)
    json.dump(data, open(f"{out}/visuals.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    open(f"{out}/preview.html", "w", encoding="utf-8").write(preview_html(data))

    need, allc = [], []
    for sec in out_secs:
        for it in sec["items"]:
            for c in it["choices"]:
                if c["kind"] != "text":
                    allc.append(c["attribution"])
                    if c["attribution_required"]:
                        need.append(c["attribution"])
    uses_pexels = any(c["source"] == "pexels" for s in out_secs for i in s["items"]
                      for c in i["choices"] if c["kind"] != "text")
    lines = ["ATTRIBUTION REQUIRED (put these in the video description):", *sorted(set(need)),
             *([PEXELS_CREDIT] if uses_pexels else []), "", "ALL SOURCES USED:", *sorted(set(allc))]
    open(f"{out}/credits.txt", "w", encoding="utf-8").write("\n".join(lines))

    tot = sum(s["requests"] for s in stats.values())
    fb = sum(s["fallback"] for s in stats.values())
    print(f"Tamam: {tot} gorsel istegi, {tot - fb} bulundu, {fb} yazi karti ({100 * (tot - fb) // max(tot, 1)}% kapsama)")
    for t, s in sorted(stats.items()):
        print(f"  {t}: {s['found']}/{s['requests']} bulundu")


if __name__ == "__main__":
    main(*sys.argv[1:])
