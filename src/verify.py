"""Dogrulayici ve duzeltmen.
Dogrulayici: bolumdeki iddialari kaynak metne karsi kontrol eder. 'supported' diyen her iddia icin
kaynaktan birebir alinti ister ve alintinin kaynakta gercekten var olup olmadigini kendisi kontrol eder.
Duzeltmen: sorunlu iddialari (celisen, desteksiz, kaynaklarin kendi arasinda celistigi) metinden temizler."""
import re
from llm import ask

BLOCKING = {"contradicted", "unsupported", "conflict"}

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
- fix: for "contradicted", the corrected sentence; for "unsupported" or "conflict", a replacement
  sentence that leaves out the problem detail, or the single word REMOVE; otherwise an empty string

Return ONLY a JSON object: {{"claims": [ ... ]}}

NARRATION:
{narration}

REFERENCE MATERIAL:
{material}
"""

REVISE_PROMPT = """You are editing one section of a YouTube script after a fact-check.
Rewrite the narration so that every problem listed below is fixed.

PROBLEMS:
{issues}

How to fix each kind:
- contradicted: replace the claim with what the evidence says.
- unsupported: delete the claim, or rephrase the sentence without the unsupported detail.
- conflict: the sources disagree about this detail, so leave the contested detail out completely.

Keep everything else: the order, the style and roughly the same length (do not shorten the section by
more than 10 percent; if you remove something, replace it with other facts from the reference material
that are not already told in the earlier sections). Spoken English for a narrator, no markdown.
Do not add any fact that is not in the reference material.

SECTION: {heading}

SCRIPT SO FAR:
{so_far}

NARRATION TO FIX:
{narration}

Return ONLY a JSON object with the single key narration.

REFERENCE MATERIAL:
{material}
"""


def norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


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


def revise_section(models, key, heading, so_far, narration, issues, material):
    lines = []
    for r in issues:
        line = f"- [{r['status']}] {r['claim']}"
        if r["evidence"]:
            line += f"\n  evidence: {r['evidence']}"
        if r["fix"]:
            line += f"\n  suggested replacement: {r['fix']}"
        lines.append(line)
    out = ask(models, key, REVISE_PROMPT.format(
        issues="\n".join(lines), heading=heading, so_far=so_far,
        narration=narration, material=material), need=["narration"], temperature=0.3)
    text = out["narration"]
    return text.strip() if isinstance(text, str) else narration
