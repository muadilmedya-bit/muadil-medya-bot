"""Dogrulayici ve duzenleyici.
- verify_section: bolumdeki iddialari kaynak metne karsi kontrol eder; 'supported' diyen her iddia icin
  kaynaktan birebir alinti ister ve alintinin kaynakta var olup olmadigini kendisi kontrol eder.
- apply_edits: sadece guvenli duzenlemeleri yapar (cakisan kaynak -> cumleyi sil; kanitli duzeltme -> degistir).
- drop_repeats: onceki bolumlerde zaten anlatilmis cumleleri siler (model kopyalasa bile)."""
import re
from llm import ask

VERIFY_PROMPT = """You are a strict fact-checker for a YouTube script.
Check the NARRATION against the REFERENCE MATERIAL only. Do not use outside knowledge.

List every checkable factual claim in the narration: names, dates, numbers, places, events,
who did what, and cause-and-effect statements. Skip opinions, analogies and rhetorical questions.
Pay special attention to dates and numbers, and to the order of events.

For each claim return an object with:
- claim: ONE full sentence copied exactly, character for character, from the narration
- verdict: one of
    "supported"    the reference material states it
    "contradicted" the reference material says something different
    "unsupported"  the reference material does not mention it
    "conflict"     different parts of the reference material disagree about this detail
- evidence: an EXACT quote of at most 25 words copied character for character from the reference
  material (for "conflict", quote one of the disagreeing passages); empty string for "unsupported".
  Never use a sentence from the narration as evidence.
- fix: for "contradicted", the corrected sentence; for "unsupported" or "conflict", the single word
  REMOVE; otherwise an empty string

Return ONLY a JSON object: {{"claims": [ ... ]}}

NARRATION:
{narration}

REFERENCE MATERIAL:
{material}
"""

ABBR = re.compile(r"(?:\b[A-Z]|\b(?:Mr|Mrs|Ms|Dr|St|vs|No|Inc|Corp|Jr|Sr))\.$")


def norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


def split_sentences(text):
    out = []
    for p in re.split(r"(?<=[.!?])\s+", text.strip()):
        if out and ABBR.search(out[-1]):
            out[-1] += " " + p
        else:
            out.append(p)
    return [p for p in out if p]


def evidence_ok(evidence, material_norm):
    """Alintinin her parcasi (... ile ayrilmis) kaynakta bulunmali."""
    ev = evidence.replace("[...]", "...").replace("\u2026", "...")
    frags = [f for f in ev.split("...") if len(norm(f).split()) >= 4]
    return bool(frags) and all(norm(f) in material_norm for f in frags)


def verify_section(models, key, narration, material, material_norm):
    out = ask(models, key, VERIFY_PROMPT.format(narration=narration, material=material),
              need=["claims"], temperature=0.1)
    results = []
    for c in out["claims"]:
        if not isinstance(c, dict):
            continue
        claim = str(c.get("claim", "")).strip()
        if not claim:
            continue
        status = str(c.get("verdict", "")).strip().lower()
        evidence = str(c.get("evidence", "")).strip()
        fix = str(c.get("fix", "")).strip()
        if status not in ("supported", "contradicted", "unsupported", "conflict"):
            status = "unverified"
        if status == "supported" and not evidence_ok(evidence, material_norm):
            status = "unverified"
        results.append({"claim": claim, "status": status, "evidence": evidence, "fix": fix})
    return results


def _tok(s):
    return re.findall(r"[A-Za-z0-9]+", s)


def safe_fix(r, material_norm):
    """Duzeltme ancak kaynak alintisi dogrulanmissa ve yeni rakam/ozel isimler alintida varsa uygulanir."""
    fix, ev = r.get("fix", "").strip(), r.get("evidence", "")
    if not fix or fix.upper() == "REMOVE" or not evidence_ok(ev, material_norm):
        return False
    new = set(_tok(fix)) - set(_tok(r["claim"]))
    if any(len(t) == 1 and not t.isdigit() for t in new):   # tek harf farki guvenilmez
        return False
    evt = set(_tok(ev))
    return all(t in evt for t in new if t.isdigit() or t[0].isupper())


def apply_edits(narration, results, material_norm):
    text, edits = narration, []
    for r in results:
        if r["status"] == "conflict":
            new = ""
        elif r["status"] == "contradicted" and safe_fix(r, material_norm):
            new = r["fix"]
        else:
            continue
        applied = r["claim"] in text
        if applied:
            text = text.replace(r["claim"], new, 1)
        edits.append({"claim": r["claim"], "status": r["status"],
                      "replacement": new, "applied": applied})
    return re.sub(r"[ \t]{2,}", " ", text).strip(), edits


def shingles(text, n=7):
    w = norm(text).split()
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def drop_repeats(narration, previous_texts, thresh=0.5):
    """Daha once anlatilmis (en az %50'si ayni) cumleleri siler."""
    seen = set()
    for t in previous_texts:
        seen |= shingles(t)
    kept, dropped = [], []
    for sent in split_sentences(narration):
        sh = shingles(sent)
        if len(sh) >= 3 and len(sh & seen) / len(sh) >= thresh:
            dropped.append(sent)
            continue
        kept.append(sent)
        seen |= sh
    return " ".join(kept), dropped
