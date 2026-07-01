"""Confidence scoring and label selection.

Combines the two signal scores into a single ``ai_probability`` and a ``confidence`` value,
then maps to one of three attribution bands. See planning.md §2 for the design rationale.
"""

from signals import llm_signal, stylometric_signal, MIN_WORDS, WORD_RE

# Signal weights (LLM weighted higher; see planning.md).
W_LLM = 0.65
W_STY = 0.35

# Attribution band thresholds — asymmetric, biased against false positives.
AI_THRESHOLD = 0.70      # need a high bar to accuse a human
HUMAN_THRESHOLD = 0.40   # lower bar for the safe direction

# Confidence for text too short to score reliably is capped here.
SHORT_TEXT_CONFIDENCE_CAP = 0.35


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _band(ai_probability):
    if ai_probability >= AI_THRESHOLD:
        return "likely_ai"
    if ai_probability <= HUMAN_THRESHOLD:
        return "likely_human"
    return "uncertain"


def score_text(text):
    """Run both signals and produce the combined verdict.

    Returns a dict with attribution, ai_probability, confidence, both signal scores,
    the LLM rationale/availability, and the raw stylometric metrics.
    """
    llm_score, llm_rationale, llm_available = llm_signal(text)
    sty_score, sty_metrics = stylometric_signal(text)

    combined = W_LLM * llm_score + W_STY * sty_score

    # Confidence = how decisive AND how much the two signals agree.
    decisiveness = 2 * abs(combined - 0.5)          # 0 at the fence, 1 at extremes
    agreement = 1 - abs(llm_score - sty_score)      # 1 = agree, 0 = opposite
    confidence = 0.6 * decisiveness + 0.4 * agreement

    word_count = len(WORD_RE.findall(text))
    too_short = word_count < MIN_WORDS

    if too_short:
        # Neither signal is reliable — force uncertain, cap confidence.
        attribution = "uncertain"
        confidence = min(confidence, SHORT_TEXT_CONFIDENCE_CAP)
    else:
        attribution = _band(combined)

    return {
        "attribution": attribution,
        "ai_probability": round(_clamp(combined), 3),
        "confidence": round(_clamp(confidence), 3),
        "llm_score": round(llm_score, 3),
        "stylometric_score": round(sty_score, 3),
        "llm_rationale": llm_rationale,
        "llm_available": llm_available,
        "stylometric_metrics": sty_metrics,
        "word_count": word_count,
        "too_short": too_short,
    }
