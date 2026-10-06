"""Gemini istemcisi: yeniden deneme, model yedekleme, gunluk kota algilama, cagri sayaci."""
import json, sys, time
import requests

RETRY_CODES = (429, 500, 502, 503, 504)
STATS = {"calls": 0, "by_model": {}, "budget": 70}


def set_budget(n):
    STATS["budget"] = int(n)


def summary():
    return {"calls": STATS["calls"], "by_model": dict(STATS["by_model"])}


def _daily_quota_hit(text):
    t = text.lower().replace("_", "").replace(" ", "").replace("-", "")
    return "perday" in t


def call_gemini(models, key, prompt, temperature=0.8):
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(1, 5):
            if STATS["calls"] >= STATS["budget"]:
                sys.exit(f"Cagri butcesi ({STATS['budget']}) asildi, is durduruldu.")
            STATS["calls"] += 1
            STATS["by_model"][model] = STATS["by_model"].get(model, 0) + 1
            try:
                r = requests.post(
                    url, params={"key": key}, timeout=180,
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json",
                                               "temperature": temperature}})
            except requests.RequestException as e:
                print(f"[{model}] baglanti hatasi: {e}")
                time.sleep(20 * attempt)
                continue
            if r.status_code == 200:
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                except (KeyError, IndexError):
                    raise ValueError("Gemini bos yanit dondurdu")
                text = "".join(p.get("text", "") for p in parts)
                print(f"Model kullanildi: {model}")
                return json.loads(text)
            print(f"[{model}] deneme {attempt}: HTTP {r.status_code}")
            if r.status_code == 429 and _daily_quota_hit(r.text):
                print(f"[{model}] gunluk kota dolu, siradaki modele geciliyor")
                break
            if r.status_code in RETRY_CODES:
                time.sleep(20 * attempt)
                continue
            break  # 404 gibi kalici hata: siradaki modele gec
    sys.exit("Hicbir model yanit vermedi.")


def ask(models, key, prompt, need=None, tries=3, temperature=0.8):
    """Gecerli JSON yaniti alana kadar dener. need: dict icinde olmasi gereken anahtarlar."""
    for _ in range(tries):
        try:
            out = call_gemini(models, key, prompt, temperature)
            if need and not (isinstance(out, dict) and all(k in out for k in need)):
                raise ValueError(f"eksik anahtar: {need}")
            return out
        except (ValueError, KeyError, IndexError, TypeError) as e:
            print(f"Yanit gecersiz ({e}), tekrar deneniyor...")
            time.sleep(10)
    sys.exit("Gemini gecerli yanit vermedi.")
