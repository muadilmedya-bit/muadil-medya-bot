"""Montaj: ses + gorseller -> video.mp4  (ffmpeg ile)
Girdi : out/voice/narration.mp3, timings.json, subtitles.srt  ve  out/visuals/visuals.json
        (yoksa data/ klasorundeki kopyalar kullanilir)
Env   : QUALITY=preview|final   SUBS=burn|none   LIMIT=bolum sayisi (0=hepsi)
"""
import json, os, re, subprocess, sys, shutil, textwrap, glob
import requests

QUALITY = os.environ.get("QUALITY", "preview").strip() or "preview"
SUBS = os.environ.get("SUBS", "burn").strip() or "burn"
LIMIT = int(os.environ.get("LIMIT", "0") or 0)
if QUALITY == "final":
    W, H, FPS, CRF = 1920, 1080, 25, "22"
else:
    W, H, FPS, CRF = 854, 480, 24, "28"
OUT = "out"; WORK = f"{OUT}/render_work"; MEDIA = f"{OUT}/media"; VID = f"{OUT}/video"
for d in (WORK, MEDIA, VID):
    os.makedirs(d, exist_ok=True)
UA = {"User-Agent": "muadil-medya-bot/1.0 (educational video project)"}
IMG_SEC, VID_MAX, CARD_SEC, CHAP_SEC = 7.0, 8.0, 3.0, 3.5

def pick(*paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def load(p):
    return json.load(open(p, encoding="utf-8"))

def run(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr[-1500:])
    return r

def probe_dur(p):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p])
    return float(r.stdout.strip())

def font():
    r = subprocess.run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"], capture_output=True, text=True)
    f = r.stdout.strip()
    if f and os.path.exists(f):
        return f
    for c in glob.glob("/usr/share/fonts/**/*.ttf", recursive=True):
        return c
    return None
FONT = font()

# ---------- girdileri bul ----------
audio = pick(f"{OUT}/voice/narration.mp3", "data/narration.mp3")
tim_p = pick(f"{OUT}/voice/timings.json", "data/timings.json")
vis_p = pick(f"{OUT}/visuals/visuals.json", "data/visuals.json")
srt_p = pick(f"{OUT}/voice/subtitles.srt", "data/subtitles.srt")
scr_p = pick("data/script.json")
if not audio or not tim_p:
    sys.exit("HATA: narration.mp3 / timings.json bulunamadi. Once 'Make voice' calismali.")
print("ses:", audio, "| zaman:", tim_p, "| gorsel:", vis_p, "| altyazi:", srt_p)
total = probe_dur(audio)
print(f"ses suresi: {total:.1f} sn")

def as_sections(d):
    if isinstance(d, dict):
        for k in ("sections", "items", "data", "timings"):
            if isinstance(d.get(k), list):
                return d[k]
        return [v for v in d.values() if isinstance(v, dict)]
    return d if isinstance(d, list) else []

tim = as_sections(load(tim_p))
vis = as_sections(load(vis_p)) if vis_p else []
script = as_sections(load(scr_p)) if scr_p else []
print("zaman bolum sayisi:", len(tim), "| gorsel bolum sayisi:", len(vis))
if tim: print("zaman ornegi:", json.dumps(tim[0], ensure_ascii=False)[:300])
if vis: print("gorsel ornegi:", json.dumps(vis[0], ensure_ascii=False)[:500])

def num(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None

def title_of(d, i):
    for k in ("title", "heading", "name"):
        if isinstance(d.get(k), str) and d[k].strip():
            return d[k].strip()
    return f"Part {i+1}"

# bolum baslangic/bitis
starts = [num(t, ("start", "start_s", "begin", "t0", "from")) for t in tim]
ends = [num(t, ("end", "end_s", "t1", "to")) for t in tim]
durs = [num(t, ("duration", "dur", "seconds", "length")) for t in tim]
if all(s is not None for s in starts) and starts:
    bounds = starts + [total]
    secs = [(bounds[i], (ends[i] if ends[i] is not None and i == len(starts) - 1 else bounds[i + 1])) for i in range(len(starts))]
elif all(d is not None for d in durs) and durs:
    t = 0.0; secs = []
    for d in durs:
        secs.append((t, t + d)); t += d
else:
    words = []
    for s in (script or tim):
        txt = json.dumps(s, ensure_ascii=False)
        words.append(max(len(txt.split()), 1))
    n = len(words) or 1
    tw = sum(words) or 1
    t = 0.0; secs = []
    for w in (words or [1]):
        d = total * w / tw; secs.append((t, t + d)); t += d
    print("UYARI: bolum zamanlari bulunamadi, kelime sayisina gore bolundu.")
secs[-1] = (secs[-1][0], max(secs[-1][1], min(total, secs[-1][1])))
if LIMIT:
    secs = secs[:LIMIT]
print("islenecek bolum:", len(secs))

# ---------- gorsel ogeleri ----------
URL_KEYS = ("url", "image_url", "file_url", "src", "download_url", "image", "file", "path")
def items_of(sec):
    """visuals.json: sections[].items[].choices[] -> duz liste (sirayla)."""
    out = []
    if not isinstance(sec, dict):
        return out
    for it in sec.get("items") or []:
        if not isinstance(it, dict):
            continue
        for c in it.get("choices") or []:
            if isinstance(c, dict):
                c = dict(c); c.setdefault("query", it.get("query", "")); out.append(c)
    return out

def classify(c):
    kind = str(c.get("kind", "")).lower()
    url = str(c.get("url") or "").strip()
    if kind == "text" or not url:
        if c.get("fallback"):
            return "skip", "", c          # bulunamayan gorsel: yazi karti gostermiyoruz
        return "card", "", c              # bilerek istenen yazi grafigi
    if kind == "video" or re.search(r"\.(mp4|webm|mov)(\?|$)", url, re.I):
        return "video", url, c
    return "image", url, c

def fetch(url, n):
    if os.path.exists(url):
        return url
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov", ".tif", ".tiff"):
        ext = ".bin"
    dst = f"{MEDIA}/m{n}{ext}"
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst
    with requests.get(url, headers=UA, timeout=60, stream=True) as r:
        r.raise_for_status()
        size = 0
        with open(dst, "wb") as f:
            for ch in r.iter_content(1 << 16):
                size += len(ch)
                if size > 150 * 1024 * 1024:
                    raise RuntimeError("dosya cok buyuk")
                f.write(ch)
    return dst

# ---------- klip uretimi ----------
VENC = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", CRF, "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]

def fade(d):
    return f",fade=t=in:st=0:d=0.4,fade=t=out:st={max(d-0.4,0):.2f}:d=0.4"

def write_text(path, text, width):
    open(path, "w", encoding="utf-8").write("\n".join(textwrap.wrap(text, width)) if text else " ")

def card(out, d, big, small="", kind="chap"):
    bg = "0x0f1b2d" if kind == "chap" else "0x16202c"
    tf1, tf2 = f"{out}.t1.txt", f"{out}.t2.txt"
    write_text(tf1, big, 28 if kind == "chap" else 34)
    write_text(tf2, small, 60)
    fs1, fs2 = int(H * 0.085), int(H * 0.04)
    ff = f":fontfile={FONT}" if FONT else ""
    vf = (f"drawtext=textfile={tf2}{ff}:fontcolor=0xf0b429:fontsize={fs2}:x=(w-text_w)/2:y=h*0.30:line_spacing=8,"
          f"drawtext=textfile={tf1}{ff}:fontcolor=white:fontsize={fs1}:x=(w-text_w)/2:y=(h-text_h)/2+h*0.04:line_spacing=12"
          + fade(d))
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={bg}:s={W}x{H}:r={FPS}:d={d:.2f}", "-vf", vf, "-t", f"{d:.2f}"] + VENC + [out])

def image_clip(out, src, d, idx):
    n = max(int(d * FPS), 2)
    mode = idx % 4
    if mode == 0:
        z, x, y = f"1+0.15*on/{n}", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
    elif mode == 1:
        z, x, y = f"1.15-0.15*on/{n}", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
    elif mode == 2:
        z, x, y = "1.12", f"(iw-iw/zoom)*on/{n}", "ih/2-ih/zoom/2"
    else:
        z, x, y = "1.12", f"(iw-iw/zoom)*(1-on/{n})", "ih/2-ih/zoom/2"
    vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:5[bg];"
          f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,"
          f"scale={W*2}:{H*2},zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},format=yuv420p" + fade(d))
    run(["ffmpeg", "-y", "-i", src, "-vf", vf, "-frames:v", str(n), "-t", f"{d:.2f}"] + VENC + [out])

def video_clip(out, src, d):
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},format=yuv420p" + fade(d)
    run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", src, "-vf", vf, "-t", f"{d:.2f}"] + VENC + [out])

clips = []; report = {"sections": [], "quality": QUALITY}; cnt = 0; mcount = 0

def add(kind, d, fn):
    """Klip uretir; hata olursa ambiyans karta duser."""
    global cnt
    cnt += 1
    out = f"{WORK}/c{cnt:04d}.mp4"
    try:
        fn(out)
    except Exception as e:
        print(f"  ! klip {cnt} ({kind}) basarisiz -> kart: {str(e)[-200:]}")
        card(out, d, "", "", "amb")
    clips.append((out, d))

for si, (s0, s1) in enumerate(secs):
    D = max(s1 - s0, 0.5)
    sec = next((v for v in vis if isinstance(v, dict) and v.get("index") == si + 1), None)
    if sec is None:
        sec = vis[si] if si < len(vis) else {}
    title = title_of(tim[si] if si < len(tim) else {}, si)
    items = [classify(x) for x in items_of(sec)]
    items = [x for x in items if x[0] != "skip"]
    print(f"[{si+1}/{len(secs)}] {title[:50]} | {D:.1f} sn | {len(items)} oge")
    plan = []  # (tur, sure, fn)
    used = 0.0
    chd = min(CHAP_SEC, D * 0.4)
    add("chapter", chd, lambda o, t=title, i=si, d=chd: card(o, d, t, f"PART {i+1}", "chap"))
    used += chd
    got = 0; k = 0; media_ok = [it for it in items if it[0] in ("image", "video")]
    cards = [it for it in items if it[0] == "card"]
    guard = 0
    while used < D - 0.05 and guard < 400:
        guard += 1
        rem = D - used
        if media_ok:
            kind, url, it = media_ok[k % len(media_ok)]; k += 1
            nm = max(len(media_ok), 1)
            base = VID_MAX if kind == "video" else min(max((D - chd) / nm, IMG_SEC), 12.0)
            d = rem if rem < base * 1.4 else base
            try:
                mcount += 1
                local = fetch(url, mcount)
            except Exception as e:
                print("  ! indirilemedi:", url[:80], str(e)[-100:])
                media_ok = [m for m in media_ok if m[1] != url]
                used_up = False
                continue
            if kind == "video":
                add("video", d, lambda o, l=local, d=d: video_clip(o, l, d))
            else:
                add("image", d, lambda o, l=local, d=d, i=cnt: image_clip(o, l, d, i))
            got += 1; used += d
        elif cards:
            kind, _, it = cards[k % len(cards)]; k += 1
            txt = str(it.get("text") or it.get("caption") or it.get("title") or it.get("query") or title)
            d = rem if rem < CARD_SEC * 1.4 else CARD_SEC
            add("card", d, lambda o, d=d, t=txt: card(o, d, t, "", "amb")); used += d
        else:
            d = rem
            add("ambient", d, lambda o, d=d, t=title: card(o, d, t, "", "amb")); used += d
    report["sections"].append({"title": title, "seconds": round(D, 1), "media_used": got, "items": len(items)})

# ---------- birlestir ----------
lst = f"{WORK}/list.txt"
with open(lst, "w") as f:
    for p, _ in clips:
        f.write(f"file '{os.path.abspath(p)}'\n")
silent = f"{WORK}/silent.mp4"
run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent])
vdur = probe_dur(silent)
final = f"{VID}/video.mp4"
vf = []
if SUBS == "burn" and srt_p:
    shutil.copy(srt_p, f"{WORK}/subs.srt")
    vf = ["-vf", "subtitles=subs.srt:force_style='FontName=DejaVu Sans,Bold=1,FontSize=14,Outline=2,Shadow=0,MarginV=18'"]
mux = ["ffmpeg", "-y", "-i", os.path.abspath(silent), "-i", os.path.abspath(audio)]
tdur = secs[-1][1] if LIMIT else total
cmd = mux + vf + ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", CRF,
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-t", f"{min(tdur, vdur):.2f}",
                  "-movflags", "+faststart", os.path.abspath(final)]
r = subprocess.run(cmd, cwd=WORK if vf else None, capture_output=True, text=True)
if r.returncode != 0:
    print(r.stderr[-1500:]); sys.exit("HATA: son birlestirme basarisiz")
report.update({"video_seconds": round(probe_dur(final), 1), "audio_seconds": round(total, 1),
               "size_mb": round(os.path.getsize(final) / 1e6, 1), "subs": bool(vf)})
json.dump(report, open(f"{VID}/render_report.json", "w"), indent=2, ensure_ascii=False)
print(json.dumps(report, indent=2, ensure_ascii=False))
