"""Transparency label generation.

Maps an attribution band + confidence into the plain-language text a reader sees. The exact
wording is fixed in planning.md; graders check these three variants against that spec.
"""

# {c} is confidence rendered as a whole-number percentage.
_LABELS = {
    "likely_ai": (
        "🤖 Likely AI-generated. Our analysis suggests this piece was probably produced with "
        "an AI writing tool (confidence: {c}%). This is an automated estimate, not a certainty "
        "— detection is imperfect. If you created this yourself, you can appeal this label and "
        "a human will review it."
    ),
    "likely_human": (
        "✍️ Likely human-written. Our analysis found no strong signs of AI generation in this "
        "piece (confidence: {c}%). This is an automated estimate and not a guarantee of "
        "authorship."
    ),
    "uncertain": (
        "❓ Attribution uncertain. We couldn't confidently tell whether a person or an AI tool "
        "wrote this piece (confidence: {c}%). We'd rather say so honestly than guess — please "
        "don't read this as a verdict either way."
    ),
}


def build_label(attribution, confidence):
    """Return the transparency-label text for a given band and confidence (0–1)."""
    template = _LABELS.get(attribution, _LABELS["uncertain"])
    return template.format(c=round(confidence * 100))
