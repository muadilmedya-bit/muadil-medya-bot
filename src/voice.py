"""Seslendirme: data/script.json -> out/voice/narration.mp3 + timings.json + subtitles.srt
Kullanim: python src/voice.py config/profile_en.yaml
Ortam degiskenleri: LIMIT (kac bolum seslendirilsin, 0 = hepsi), VOICE (ses adi, bos = ayar dosyasi)
Not: Edge-TTS resmi olmayan bir servistir. Bozulursa bu dosyadaki synth() fonksiyonu baska bir
motorla (ornegin Piper) degistirilebilir, geri kalan her sey aynen calisir."""
import os, sys, re, json, asyncio, subprocess, wave, hashlib, textwrap
import yaml
from verify import split_sentences

RATE_HZ = 24000
GAP_SENTENCE = 0.28     # cumleler arasi sessizlik (sn)
GAP_SECTION = 0.90      # bolumler arasi sessizlik
LEAD = 0.50             # videonun basindaki sessizlik
CONCURRENCY = 3
FALLBACK_VOICES = ["en-US-AndrewNeural", "en-US-GuyNeural", "en-GB-RyanNeural", "en-US-AriaNeural"]

# Kisaltmalarin dogru okunmasi icin (altyazida orijinal metin kalir). Yenilerini
# config/pronunciations.json dosyasina {"KISALTMA": "okunus"} olarak ekleyebilirsin.
DEFAULT_PRONUNCIATIONS = {
    "ARPANET": "ARPA net", "NSFNET": "N S F net", "CSNET": "C S net", "ALOHAnet": "Aloha net",
    "TCP/IP": "T C P I P", "TCP": "T C P", "IP": "I P", "IMPs": "I M Ps", "IMP": "I M P",
    "UCLA": "U C L A", "SRI": "S R I", "BBN": "B B N", "NPL": "N P L", "SDS": "S D S",
    "AT&T": "A T and T", "SOSP": "S O S P", "ISO": "I S O", "Wi-Fi": "Why Fi",
    "DDP-516": "D D P five sixteen", "Q-32": "Q thirty two",
}


def load_pronunciations():
    d = dict(DEFAULT_PRONUNCIATIONS)
    if os.path.exists("config/pronunciations.json"):
        d.update(json.load(open("config/pronunciations.json", encoding="utf-8")))
    return d


def to_spoken(text, pron):
    text = text.replace("\u2014", ", ").replace("\u2026", "...").replace("\u2019", "'")
    for k in sorted(pron, key=len, reverse=True):
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])", pron[k], text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- ses motoru (degistirilebilir kisim)
async def synth(text, voice, rate, path):
    """Tek bir metni mp3 dosyasina cevirir. Edge-TTS yerine baska motor yazilacaksa sadece burasi degisir."""
    import edge_tts
    last = None
    for attempt in range(1, 6):
        try:
            await edge_tts.Communicate(text, voice, rate=rate).save(path)
            if os.path.exists(path) and os.path.getsize(path) > 500:
                return
            raise RuntimeError("bos ses dosyasi")
        except Exception as e:  # ag hatasi, hiz siniri, bos yanit...
            last = e
            print(f"  TTS hatasi ({attempt}/5): {type(e).__name__}: {e}")
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError(f"Seslendirme basarisiz oldu: {last}")


async def pick_voice(candidates, rate, workdir):
    for v in candidates:
        try:
            await synth("Hello. This is a voice test.", v, rate, os.path.join(workdir, "_probe.mp3"))
            return v
        except Exception as e:
            print(f"Ses kullanilamadi ({v}): {e}")
    sys.exit("Hicbir ses calismadi. Edge-TTS erisilemiyor olabilir.")
# ----------------------------------------------------------------------------------------------


def to_wav(mp3, wav):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ar", str(RATE_HZ),
                    "-ac", "1", "-f", "wav", wav], check=True)


def read_frames(wav):
    with wave.open(wav) as w:
        assert w.getframerate() == RATE_HZ and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


def silence(sec):
    return bytes(int(sec * RATE_HZ) * 2)


def ts(sec):
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def split_cues(text, max_chars=76):
    cues, cur = [], []
    for w in text.split():
        if cur and len(" ".join(cur + [w])) > max_chars:
            cues.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        cues.append(" ".join(cur))
    return cues


def build_srt(sections):
    lines, n = [], 0
    for sec in sections:
        for s in sec["sentences"]:
            cues = split_cues(s["text"])
            total = sum(len(c) for c in cues) or 1
            t = s["start"]
            for c in cues:
                d = (s["end"] - s["start"]) * len(c) / total
                n += 1
                lines += [str(n), f"{ts(t)} --> {ts(t + d)}", "\n".join(textwrap.wrap(c, 40)), ""]
                t += d
    return "\n".join(lines)


async def run_all(todo, voice, rate):
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def one(item):
        nonlocal done
        async with sem:
            await synth(item["spoken"], voice, rate, item["mp3"])
        done += 1
        if done % 10 == 0 or done == len(todo):
            print(f"  {done}/{len(todo)} cumle seslendirildi")

    await asyncio.gather(*(one(it) for it in todo))


def main(profile_path="config/profile_en.yaml"):
    cfg = yaml.safe_load(open(profile_path, encoding="utf-8"))
    script = json.load(open("data/script.json", encoding="utf-8"))
    limit = int(os.environ.get("LIMIT", "0") or 0)
    secs = script["sections"][:limit] if limit else script["sections"]
    requested = os.environ.get("VOICE", "").strip() or cfg.get("tts_voice", FALLBACK_VOICES[0])
    rate = cfg.get("tts_rate", "+0%")
    out, work = "out/voice", "out/work"
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    pron = load_pronunciations()

    voice = asyncio.run(pick_voice([requested] + [v for v in FALLBACK_VOICES if v != requested], rate, work))
    print(f"Ses: {voice}, hiz: {rate}, bolum sayisi: {len(secs)}")

    plan, todo = [], []
    for si, sec in enumerate(secs, 1):
        sents = []
        for sent in split_sentences(sec["narration"]):
            if not re.search(r"[A-Za-z0-9]", sent):
                continue
            spoken = to_spoken(sent, pron)
            key = hashlib.md5(f"{voice}|{rate}|{spoken}".encode()).hexdigest()[:14]
            item = {"text": sent, "spoken": spoken, "mp3": os.path.join(work, key + ".mp3")}
            sents.append(item)
            if not os.path.exists(item["mp3"]):
                todo.append(item)
        plan.append({"index": si, "heading": sec["heading"], "sentences": sents})
    print(f"{sum(len(p['sentences']) for p in plan)} cumle, {len(todo)} tanesi yeni seslendirilecek")
    if todo:
        asyncio.run(run_all(todo, voice, rate))

    audio = bytearray(silence(LEAD))
    t = LEAD
    sections_out, words, slow_fast = [], 0, []
    for p in plan:
        sec_start = t
        sents_out = []
        for k, it in enumerate(p["sentences"]):
            wav = it["mp3"][:-4] + ".wav"
            if not os.path.exists(wav):
                to_wav(it["mp3"], wav)
            frames = read_frames(wav)
            dur = len(frames) / 2 / RATE_HZ
            if dur / max(len(it["text"]), 1) < 0.03:
                slow_fast.append(it["text"][:60])
            sents_out.append({"text": it["text"], "start": round(t, 3), "end": round(t + dur, 3)})
            audio += frames
            t += dur
            words += len(it["text"].split())
            gap = GAP_SENTENCE if k < len(p["sentences"]) - 1 else GAP_SECTION
            audio += silence(gap)
            t += gap
        sections_out.append({"index": p["index"], "heading": p["heading"],
                             "start": round(sec_start, 3), "end": round(t - GAP_SECTION, 3),
                             "sentences": sents_out})

    full_wav = os.path.join(work, "full.wav")
    with wave.open(full_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE_HZ)
        w.writeframes(bytes(audio))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", full_wav, "-codec:a", "libmp3lame",
                    "-b:a", "128k", os.path.join(out, "narration.mp3")], check=True)

    total = len(audio) / 2 / RATE_HZ
    wpm = words / (total / 60)
    timings = {"voice": voice, "rate": rate, "sample_rate": RATE_HZ, "total_seconds": round(total, 2),
               "words": words, "words_per_minute": round(wpm, 1), "sections": sections_out}
    json.dump(timings, open(os.path.join(out, "timings.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    open(os.path.join(out, "subtitles.srt"), "w", encoding="utf-8").write(build_srt(sections_out))

    print(f"Tamam: {total / 60:.1f} dakika ses, {words} kelime, {wpm:.0f} kelime/dk")
    if wpm > 185 or wpm < 120:
        print("UYARI: konusma hizi olagan disi, sesi dinleyip ayarlayin (config: tts_rate).")
    if slow_fast:
        print("UYARI: cok hizli/kisa cikan cumleler (eksik okunmus olabilir):", slow_fast[:5])


if __name__ == "__main__":
    main(*sys.argv[1:])
