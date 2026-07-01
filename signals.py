"""The two detection signals.

Signal 1 (semantic):   ``llm_signal``          — Groq llama-3.3-70b-versatile.
Signal 2 (structural): ``stylometric_signal``  — pure-Python statistics.

Both return P(AI) in [0, 1] where 1.0 means "almost certainly AI-generated".
"""

import json
import os
import re
import statistics

# ---------------------------------------------------------------------------
# Shared lexicons
# ---------------------------------------------------------------------------

# Connective / boilerplate phrases LLMs overuse. Presence pushes toward "AI".
AI_MARKER_PHRASES = [
    "it is important to note", "it is worth noting", "furthermore", "moreover",
    "in conclusion", "in summary", "additionally", "delve into", "delve", "tapestry",
    "navigate the", "in today's", "ever-evolving", "landscape", "paradigm shift",
    "it is essential", "plays a crucial role", "a testament to", "underscores",
    "when it comes to", "as a result", "stakeholders", "leverage", "robust",
]

# Casual markers that push toward "human".
CASUAL_MARKERS = [
    "honestly", "lol", "tbh", "gonna", "wanna", "kinda", "sorta", "yeah",
    "ok so", "idk", "imo", "like,", "y'know", "whatever", "ugh", "meh",
]

CONTRACTION_RE = re.compile(r"\b\w+['’](t|s|re|ve|ll|d|m)\b", re.IGNORECASE)
LONE_LOWER_I_RE = re.compile(r"(?<![A-Za-z])i(?![A-Za-z])")
WORD_RE = re.compile(r"[A-Za-z']+")
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")

MIN_WORDS = 40  # below this, neither signal is reliable (see planning.md edge case 3)


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# Signal 2: Stylometric heuristics (pure Python)
# ---------------------------------------------------------------------------

def stylometric_signal(text):
    """Structural analysis of the prose.

    Returns (score, metrics) where score in [0,1] is P(AI) and metrics is a dict of the
    raw sub-metrics for transparency / debugging.
    """
    words = WORD_RE.findall(text)
    total_words = len(words)
    sentences = [s.strip() for s in SENTENCE_RE.findall(text) if s.strip()]
    sent_lengths = [len(WORD_RE.findall(s)) for s in sentences if WORD_RE.findall(s)]

    # --- sub-metric: sentence-length burstiness (human = bursty = high) ---
    if len(sent_lengths) >= 2 and statistics.mean(sent_lengths) > 0:
        burstiness = statistics.pstdev(sent_lengths) / statistics.mean(sent_lengths)
    else:
        burstiness = 0.0
    # Low burstiness -> AI. Map: burstiness ~0.6+ is very human, ~0.1 very AI.
    burst_ai = _clamp(1.0 - (burstiness / 0.6))

    # --- sub-metric: type-token ratio (human = diverse = high TTR) ---
    ttr = len(set(w.lower() for w in words)) / total_words if total_words else 0.0
    # For passages of this size, TTR ~0.75+ is diverse/human, ~0.4 is repetitive/AI-ish.
    ttr_ai = _clamp((0.75 - ttr) / 0.35)

    # --- sub-metric: contraction & informality rate (human = high) ---
    contractions = len(CONTRACTION_RE.findall(text))
    lone_i = len(LONE_LOWER_I_RE.findall(text))
    casual_hits = sum(text.lower().count(m) for m in CASUAL_MARKERS)
    informal_per_100 = (contractions + lone_i + casual_hits) / max(total_words, 1) * 100
    # ~2+ informal markers per 100 words is clearly human; 0 leans AI.
    informal_ai = _clamp(1.0 - (informal_per_100 / 2.0))

    # --- sub-metric: AI-marker phrase density (human = low) ---
    lowered = text.lower()
    marker_hits = sum(lowered.count(p) for p in AI_MARKER_PHRASES)
    markers_per_100 = marker_hits / max(total_words, 1) * 100
    # ~1.5+ marker phrases per 100 words is strongly AI-flavored.
    marker_ai = _clamp(markers_per_100 / 1.5)

    # --- sub-metric: punctuation variety (human = varied = high) ---
    punct_kinds = sum(1 for ch in set("-—…();:?!\"") if ch in text)
    variety_ai = _clamp(1.0 - (punct_kinds / 5.0))

    # Weighted blend of sub-scores.
    weights = {
        "burstiness": 0.30,
        "ttr": 0.20,
        "informality": 0.25,
        "ai_markers": 0.15,
        "punct_variety": 0.10,
    }
    score = (
        weights["burstiness"] * burst_ai
        + weights["ttr"] * ttr_ai
        + weights["informality"] * informal_ai
        + weights["ai_markers"] * marker_ai
        + weights["punct_variety"] * variety_ai
    )

    metrics = {
        "burstiness": round(burstiness, 3),
        "type_token_ratio": round(ttr, 3),
        "informal_per_100w": round(informal_per_100, 2),
        "ai_markers_per_100w": round(markers_per_100, 2),
        "punct_variety_kinds": punct_kinds,
        "sub_scores": {
            "burstiness_ai": round(burst_ai, 3),
            "ttr_ai": round(ttr_ai, 3),
            "informality_ai": round(informal_ai, 3),
            "ai_markers_ai": round(marker_ai, 3),
            "punct_variety_ai": round(variety_ai, 3),
        },
    }
    return round(_clamp(score), 3), metrics


# ---------------------------------------------------------------------------
# Signal 1: LLM classification (Groq)
# ---------------------------------------------------------------------------

_GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

_LLM_SYSTEM_PROMPT = (
    "You are an AI-content detector for a creative writing platform. Judge whether a passage "
    "reads as AI-generated or human-written based on voice, coherence patterns, over-smoothness, "
    "generic framing, and hedging. Be calibrated and cautious: formal or non-native human "
    "writing is NOT necessarily AI. Respond ONLY with strict JSON of the form "
    '{"ai_probability": <float 0..1>, "rationale": "<one short sentence>"}. '
    "1.0 means almost certainly AI, 0.0 means almost certainly human."
)


def _fallback_llm(text):
    """Heuristic used when no GROQ_API_KEY is configured or the API call fails.

    Independent-ish surface-formality proxy so the app is fully testable offline. This is a
    stand-in for the real semantic signal and is flagged (llm_available=False) everywhere it is
    used, so it never masquerades as the real model.
    """
    lowered = text.lower()
    words = WORD_RE.findall(text)
    n = max(len(words), 1)
    marker_hits = sum(lowered.count(p) for p in AI_MARKER_PHRASES)
    casual_hits = sum(lowered.count(m) for m in CASUAL_MARKERS)
    contractions = len(CONTRACTION_RE.findall(text))
    score = 0.5 + 0.12 * marker_hits / (n / 100 + 1e-9) \
        - 0.10 * (casual_hits + contractions) / (n / 100 + 1e-9)
    score = _clamp(score)
    return score, "Offline heuristic estimate (Groq key not configured)."


def llm_signal(text):
    """Semantic signal via Groq. Returns (score, rationale, available: bool).

    Falls back to a local heuristic (available=False) when no key is set or the call errors.
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key or api_key == "your_key_here":
        score, rationale = _fallback_llm(text)
        return score, rationale, False

    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=_GROQ_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": f"Passage:\n\n{text}"},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        score = _clamp(float(data.get("ai_probability", 0.5)))
        rationale = str(data.get("rationale", ""))[:300]
        return score, rationale, True
    except Exception as exc:  # network error, bad JSON, auth, etc. — degrade gracefully.
        score, rationale = _fallback_llm(text)
        return score, f"{rationale} (Groq error: {type(exc).__name__})", False
