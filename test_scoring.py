"""Calibration harness — run the 4 deliberately chosen inputs from the project spec through
the scoring pipeline and print each signal separately so we can see where they diverge.

Usage:  python test_scoring.py
(Runs offline with the heuristic LLM fallback if no GROQ_API_KEY is set.)
"""

from dotenv import load_dotenv
load_dotenv()

from labels import build_label
from scoring import score_text

CASES = {
    "clearly_ai": (
        "Artificial intelligence represents a transformative paradigm shift in modern society. "
        "It is important to note that while the benefits of AI are numerous, it is equally "
        "essential to consider the ethical implications. Furthermore, stakeholders across "
        "various sectors must collaborate to ensure responsible deployment."
    ),
    "clearly_human": (
        "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the "
        "broth was fine but they put WAY too much sodium in it and i was thirsty for like three "
        "hours after. my friend got the spicy version and said it was better. probably won't go "
        "back unless someone drags me there"
    ),
    "borderline_formal_human": (
        "The relationship between monetary policy and asset price inflation has been extensively "
        "studied in the literature. Central banks face a fundamental tension between their "
        "mandate for price stability and the unintended consequences of prolonged low interest "
        "rates on equity and real estate valuations."
    ),
    "borderline_edited_ai": (
        "I've been thinking a lot about remote work lately. There are genuine tradeoffs — "
        "flexibility and no commute on one side, isolation and blurred work-life boundaries on "
        "the other. Studies show productivity varies widely by individual and role type."
    ),
}


def main():
    print(f"{'case':<26} {'llm':>5} {'sty':>5} {'combined':>9} {'conf':>5}  attribution")
    print("-" * 80)
    for name, text in CASES.items():
        r = score_text(text)
        print(f"{name:<26} {r['llm_score']:>5.2f} {r['stylometric_score']:>5.2f} "
              f"{r['ai_probability']:>9.2f} {r['confidence']:>5.2f}  {r['attribution']}")
    print()
    print("Label previews:")
    for band in ("likely_ai", "uncertain", "likely_human"):
        print(f"\n[{band}]")
        print("  " + build_label(band, 0.88))


if __name__ == "__main__":
    main()
