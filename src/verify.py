"""Dogrulayici: bolumdeki iddialari kaynak metne karsi kontrol eder.
Her 'supported' iddia icin kaynaktan birebir alinti istenir ve alintinin kaynakta
gercekten var olup olmadigi program tarafindan kontrol edilir."""
import re
from llm import ask

VERIFY_PROMPT = """You are a strict fact-checker for a YouTube script.
Check the NARRATION against the REFERENCE MATERIAL only. Do not use outside knowledge.

List every checkable factual claim in the narration: names, dates, numbers, places, events,
who did what, and cause-and-effect statements. Skip opinions, analogies and rhetorical questions.
Pay special attention to dates and numbers, and to the order of events.

For each claim return an object with:
- claim: ONE full sentence copied exactly, character for character, from the narration
- verdict: "supported" if the reference material states it, "contradicted" if the reference material
  says something different, "unsupported" if the reference material does not mention it
- evidence: for "supported" or "contradicted", an EXACT quote of at most 25 words copied character for
  character from the reference material; otherwise an empty string
- fix: for "contradicted" or "unsupported", a replacement for that whole sentence that uses only the
  reference material, or the single word REMOVE if it cannot be fixed; otherwise an empty string

Return ONLY a JSON object: {{"claims": [ ... ]}}

NARRATION:
{narration}

REFERENCE MATERIAL:
{material}
"""


def norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


def verify_section(models, key, narration, material, material_norm):
    out = ask(models, key, VERIFY_PROMPT.format(narration=narration, material=material),
              need=["claims"], temperature=0.1)
    results, fixed = [], narration
    for c in out["claims"]:
        if not isinstance(c, dict):
            continue
        claim = str(c.get("claim", "")).strip()
        if not claim:
            continue
        status = str(c.get("verdict", "")).strip().lower()
        evidence = str(c.get("evidence", "")).strip()
        fix = str(c.get("fix", "")).strip()
        if status == "supported" and (not evidence or norm(evidence) not in material_norm):
            status = "quote_not_found"
        entry = {"claim": claim, "status": status, "evidence": evidence,
                 "fix": fix, "auto_fixed": False}
        if status == "contradicted" and fix and claim in fixed:
            fixed = fixed.replace(claim, "" if fix.upper() == "REMOVE" else fix, 1)
            entry["auto_fixed"] = True
        results.append(entry)
    fixed = re.sub(r"[ \t]{2,}", " ", fixed).strip()
    return fixed, results
