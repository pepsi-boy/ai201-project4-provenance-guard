# Provenance Guard

A backend system a creative-sharing platform can plug into to **classify submitted text as
human- or AI-written, score its confidence honestly, surface a plain-language transparency
label to readers, and let creators appeal a classification.** Two independent detection
signals, calibrated confidence that communicates uncertainty instead of forcing a binary
verdict, rate limiting, and a structured audit log.

Built for CodePath AI201 — Project 4. Design spec lives in [planning.md](planning.md).

> **Guiding principle:** on a writing platform, **a false positive (labeling a human's work as
> AI) is worse than a false negative.** The scoring bands, the confidence formula, and every
> "likely AI" label's appeal path are all shaped by that asymmetry.

---

## Quick start

```bash
git clone https://github.com/pepsi-boy/ai201-project4-provenance-guard.git
cd ai201-project4-provenance-guard

python -m venv .venv
source .venv/bin/activate          # Mac/Linux
pip install -r requirements.txt

cp .env.example .env               # then edit .env and add your GROQ_API_KEY
python app.py                      # serves on http://localhost:5000
# macOS: port 5000 is often taken by AirPlay — use  PORT=5001 python app.py
```

The system **runs fully offline without a Groq key** using a documented heuristic fallback for
the LLM signal (every response flags `"llm_available": false` so the stand-in never
masquerades as the real model). Add a real `GROQ_API_KEY` to activate the semantic signal —
scores sharpen noticeably, especially on literary human prose.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/submit` | Classify a piece of text. Rate-limited. |
| `POST` | `/appeal` | Contest a classification (flips status to `under_review`). |
| `GET`  | `/log` | Recent audit-log events (JSON). `?limit=N` optional. |
| `GET`  | `/appeals` | Reviewer queue of items currently `under_review`. |
| `GET`  | `/health` | Liveness + whether a Groq key is configured. |

---

## Architecture overview — the path a submission takes

```
POST /submit {text, creator_id}
   │
   ▼  input guard (empty? <40 words → forced "uncertain")
   ├─────────────┬───────────────┐
   ▼             ▼               │  raw text fans out to both signals
SIGNAL 1: LLM   SIGNAL 2: Stylometry
(Groq, semantic) (pure Python, structural)
   │ llm_score      │ stylometric_score        both are P(AI) ∈ [0,1]
   └──────┬─────────┘
          ▼  CONFIDENCE SCORING
             combined      = 0.65·llm + 0.35·sty
             confidence    = 0.6·decisiveness + 0.4·agreement
             attribution   = band(combined)   → likely_ai / uncertain / likely_human
          ▼  TRANSPARENCY LABEL  (band + confidence → one of 3 plain-language texts)
          ├────────────────────┬──────────────────┐
          ▼                    ▼                   ▼
   AUDIT LOG (SQLite)   submissions row      JSON response
   append event         (current state)      {content_id, attribution,
                                               confidence, label, signals}
```

A submission is guarded for length, scored by **two independent signals**, combined into a
single `ai_probability` plus a `confidence` value that folds in both how decisive the score is
and how much the two signals agree, mapped to one of three attribution bands, turned into a
transparency label, written to the audit log, and returned as JSON. The `content_id` in that
response is the handle a creator later uses to appeal. See [planning.md](planning.md) for the
full diagram including the appeal flow.

---

## Detection signals

The pipeline uses **two genuinely distinct signals** — one semantic, one structural. They fail
differently, which is the whole reason to combine them.

### Signal 1 — LLM classification (Groq `llama-3.3-70b-versatile`) · *semantic*
- **Measures:** a holistic read of whether the text *reads* as AI-generated — over-smooth
  coherence, generic framing, hedging, "AI voice." Returns `P(AI) ∈ [0,1]` + a one-line rationale.
- **Why:** captures meaning and stylistic coherence that no counting heuristic can. This is the
  strongest single signal on ordinary prose, so it carries the higher weight (0.65).
- **What it misses:** unreliable on very short text; **biased against formal and non-native human
  writers** (the core false-positive risk); non-deterministic; can produce a confident but wrong
  rationale; and has no ground truth — it pattern-matches an "AI voice" that humans can imitate
  and AI can be prompted to avoid.

### Signal 2 — Stylometric heuristics (pure Python) · *structural*
- **Measures:** statistical structure of the prose, blended into one `P(AI) ∈ [0,1]`:
  - **Sentence-length burstiness** — humans vary sentence length; AI trends uniform.
  - **Type-token ratio** — vocabulary diversity; AI reuses a narrower vocabulary.
  - **Contraction & informality rate** — casual human registers use far more.
  - **AI-marker phrase density** — boilerplate connectives AI overuses ("furthermore", "it is
    important to note", "leverage", "robust"…).
  - **Punctuation variety** — humans reach for dashes, ellipses, parentheses more freely.
- **Why:** completely independent of meaning — it counts. A confident-but-wrong LLM verdict can
  be pulled back toward "uncertain" by structure, and vice versa.
- **What it misses:** fooled by any uniform, formal human writing (academic, legal, non-native);
  meaningless on text under ~40 words (variance undefined) and on poetry/lists/code; and it can't
  read meaning at all — a statistically "human" but incoherent text sails through.

**Combination:** `ai_probability = 0.65 · llm_score + 0.35 · stylometric_score`. Implemented in
[scoring.py](scoring.py); each signal in [signals.py](signals.py).

---

## Confidence scoring

A raw combined probability isn't enough — the system must say **how sure it is**. `confidence`
answers "how sure are we of *whatever* verdict we give?" and is high only when the score is
**decisive** *and* the **two signals agree**:

```python
combined     = 0.65 * llm_score + 0.35 * stylometric_score
decisiveness = 2 * abs(combined - 0.5)              # 0 at the fence (0.5), 1 at the extremes
agreement    = 1 - abs(llm_score - stylometric_score)   # 1 = signals agree, 0 = opposite
confidence   = 0.6 * decisiveness + 0.4 * agreement
```

So two submissions with the *same* `ai_probability` can carry *different* confidence: if the
signals disagree, confidence drops — which is exactly the honesty a creator deserves.

### Attribution bands — asymmetric on purpose

| `ai_probability` | Attribution | Why this cutoff |
|------------------|-------------|-----------------|
| `≥ 0.70` | `likely_ai` | **High bar** — we don't accuse a human lightly. |
| `≤ 0.40` | `likely_human` | Lower bar — the safe direction. |
| `0.40 – 0.70` | `uncertain` | The honest default; the band is wider on the AI side. |

Text under 40 words is force-routed to `uncertain` with capped confidence, because neither
signal is reliable at that length. `0.5` therefore lands squarely in `uncertain` and produces
the uncertain label — never a coin-flip verdict.

### How I validated it's meaningful

I built [test_scoring.py](test_scoring.py), a calibration harness running the four deliberately
chosen inputs from the spec (clear-AI, clear-human, formal-human, edited-AI) and printing each
signal separately. Scores span the full range and map to all three bands, and the
false-positive-prone formal-human case correctly holds at `uncertain` rather than accusing the
writer. I checked that clearly different inputs produce clearly different scores and that no
input flips at a bare 0.5 boundary.

### Two example submissions with noticeably different confidence

Both are **live `/submit` responses with the Groq LLM signal active** (`llm_available: true`). The
LLM is non-deterministic, so exact values drift by ~0.05–0.1 between runs; the *gap* is the point.

**High-confidence case** — casual first-person human writing:
> *"ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth
> was fine but they put WAY too much sodium in it…"*

```json
{ "attribution": "likely_human", "ai_probability": 0.158, "confidence": 0.762,
  "llm_score": 0.2, "stylometric_score": 0.08 }
```

**Lower-confidence case** — formal, uniform prose from a (stated) non-native academic writer:
> *"The relationship between monetary policy and asset price inflation has been extensively
> studied in the literature. Central banks face a fundamental tension…"*

```json
{ "attribution": "uncertain", "ai_probability": 0.638, "confidence": 0.494,
  "llm_score": 0.7, "stylometric_score": 0.522 }
```

The confidence gap (0.76 vs 0.49) is the point: the system is loud when it's sure and openly
hesitant on exactly the kind of formal human writing that would otherwise be a false positive.
Note the second case is the false-positive guard working end-to-end — the **LLM alone wanted to
call it AI (0.7)**, but the high 0.70 AI-band threshold plus a milder stylometric vote (0.52)
held the verdict at `uncertain` instead of accusing the writer.

---

## Transparency label

The label is what a reader actually sees. It states the result in plain language, makes
confidence meaningful to a non-technical reader, and never overclaims certainty. `{c}` is
confidence as a whole-number percent. All three variants are implemented in [labels.py](labels.py).

**High-confidence AI (`likely_ai`):**
> 🤖 Likely AI-generated. Our analysis suggests this piece was probably produced with an AI
> writing tool (confidence: {c}%). This is an automated estimate, not a certainty — detection is
> imperfect. If you created this yourself, you can appeal this label and a human will review it.

**High-confidence human (`likely_human`):**
> ✍️ Likely human-written. Our analysis found no strong signs of AI generation in this piece
> (confidence: {c}%). This is an automated estimate and not a guarantee of authorship.

**Uncertain (`uncertain`):**
> ❓ Attribution uncertain. We couldn't confidently tell whether a person or an AI tool wrote
> this piece (confidence: {c}%). We'd rather say so honestly than guess — please don't read this
> as a verdict either way.

Only the `likely_ai` label surfaces the appeal path prominently, because that's the label that
can harm a creator. (Appeals are technically accepted on any classification.)

---

## Appeals workflow

`POST /appeal` with `{content_id, creator_reasoning}`:
1. Looks up the submission (`404` if the `content_id` is unknown).
2. Sets its status to `under_review` and stores the creator's reasoning.
3. Appends an `appeal` event to the audit log **alongside** the original classification.
4. Returns a confirmation. (No automated re-classification — a human reviewer decides.)

Reviewers read the queue via `GET /appeals`, which shows each item's original attribution, both
signal scores, confidence, the creator's reasoning, and timestamps.

```bash
curl -s -X POST http://localhost:5001/appeal -H "Content-Type: application/json" \
  -d '{"content_id":"PASTE-ID","creator_reasoning":"I wrote this myself; English is my second language and my academic register is formal by training."}'
```
```json
{
  "message": "Appeal received. This content is now under review by a human moderator.",
  "content_id": "f45c6774-a1fb-492c-b9fb-513e1bcd2d08",
  "status": "under_review",
  "original_attribution": "likely_ai",
  "original_confidence": 0.604,
  "creator_reasoning": "I wrote this for a graduate seminar. English is my second language..."
}
```

---

## Rate limiting

Applied to `POST /submit` via Flask-Limiter (in-memory store):

```python
@limiter.limit("10 per minute;100 per day")
```

**Reasoning for these specific numbers.** On a writing platform a genuine creator submits their
own finished work — a handful of pieces in a session at most, even accounting for re-submitting
after an edit. **10 per minute** comfortably covers that bursty-but-human pattern while stopping
a script from hammering the (LLM-backed, non-free-to-run) endpoint. **100 per day** caps
sustained abuse from a single IP — an adversary trying to slowly flood the platform or probe the
detector — while staying generous for a power user posting a lot in one day. The daily cap is the
backstop the per-minute cap can't provide.

**Evidence** — 12 rapid requests against a fresh server (first 10 pass, then `429`):
```
200 200 200 200 200 200 200 200 200 200 429 429
```

---

## Audit log

Every decision is written to a structured SQLite audit log (append-only `audit_log` table plus a
current-state `submissions` table). `GET /log` surfaces it as JSON. Each entry carries timestamp,
content ID, creator, event type (`classification` | `appeal`), attribution, combined confidence,
**both individual signal scores**, status, and a detail blob (LLM rationale / stylometric metrics /
appeal reasoning). In production `/log` would require auth; here it's for grading visibility.

Real sample — three classifications and one appeal (trimmed to key fields; full detail blobs
included in the live endpoint):

```json
{ "entries": [
  { "id": 4, "event_type": "appeal", "content_id": "f45c6774-…", "creator_id": "acct-9931",
    "attribution": "likely_ai", "confidence": 0.604, "ai_probability": 0.734,
    "llm_score": 0.8, "stylometric_score": 0.61, "status": "under_review",
    "detail": { "appeal_reasoning": "I wrote this for a graduate seminar. English is my second language…" },
    "timestamp": "2026-07-01T06:02:49.922Z" },

  { "id": 3, "event_type": "classification", "content_id": "63be38b9-…", "creator_id": "prof-lee",
    "attribution": "uncertain", "confidence": 0.494, "ai_probability": 0.638,
    "llm_score": 0.7, "stylometric_score": 0.522, "status": "classified",
    "timestamp": "2026-07-01T06:02:49.894Z" },

  { "id": 2, "event_type": "classification", "content_id": "f45c6774-…", "creator_id": "acct-9931",
    "attribution": "likely_ai", "confidence": 0.604, "ai_probability": 0.734,
    "llm_score": 0.8, "stylometric_score": 0.61, "status": "classified",
    "timestamp": "2026-07-01T06:02:49.880Z" },

  { "id": 1, "event_type": "classification", "content_id": "d4527041-…", "creator_id": "maya",
    "attribution": "likely_human", "confidence": 0.762, "ai_probability": 0.158,
    "llm_score": 0.2, "stylometric_score": 0.08, "status": "classified",
    "timestamp": "2026-07-01T06:02:49.846Z" }
] }
```

Note how entries `#2` and `#4` share a `content_id`: the appeal is logged right next to the
original decision, which is exactly what a reviewer needs.

---

## Known limitations

- **Formal / non-native human writing is the system's worst case.** Uniform sentence length,
  formal connectives, and few contractions push *both* signals toward AI — the stylometric signal
  by construction, and the LLM because "AI voice" and "careful ESL academic voice" overlap. This
  is a direct property of the signals, not a data-volume problem: structural uniformity is
  genuinely ambiguous. It's why the AI band starts at a high 0.70, why formal prose lands in
  `uncertain` (see the monetary-policy example above), and why every AI label ships an appeal path.
- **Repetitive, simple-vocabulary poetry** trips the same wires — low burstiness and low
  type-token ratio look "AI" to stylometry even though repetition is a deliberate human craft.
- **Non-prose (code, lists, tables)** breaks the stylometric assumptions entirely and is
  out-of-scope for reliable scoring.
- **Offline mode** (no Groq key) replaces the semantic signal with a surface heuristic; it's
  weaker on literary human prose and is always flagged `llm_available: false`.

Detection is deliberately *not* framed as perfect — the honest handling of uncertainty and the
appeal path are the real product.

---

## Spec reflection

- **How the spec helped:** writing the confidence formula and the three band thresholds in
  planning.md *before* coding forced me to decide what `0.5` should mean to a user (genuine "can't
  tell") first, then implement toward it. When I wrote [scoring.py](scoring.py) the logic was
  essentially transcription — no mid-build waffling over whether 0.62 was "AI" or "uncertain."
- **How the implementation diverged:** the spec framed `confidence` mostly as distance from the
  fence (decisiveness). While testing, I saw that two signals *disagreeing* should also lower
  confidence, so I added the `agreement` term (`0.6·decisiveness + 0.4·agreement`). The spec's
  intent — communicate genuine uncertainty — is better served by penalizing signal disagreement,
  so I updated the design to match.

---

## AI usage

- **Instance 1 — stylometric signal.** I directed an AI tool to draft `stylometric_signal(text)`
  given my planning.md sub-metric list (burstiness, TTR, informality, AI-marker density,
  punctuation variety). It produced reasonable metric computations but its normalization mapped
  each metric with arbitrary cutoffs that didn't reflect my spec's "AI = uniform" reasoning. I
  rewrote the normalization so every sub-metric maps explicitly to an "AI-likeness" contribution
  with documented thresholds, and set the blend weights myself (burstiness 0.30, TTR 0.20, …).
- **Instance 2 — confidence scoring.** I asked it to implement `score_text` combining the signals
  per my spec. Its first version computed confidence as plain `2·|combined − 0.5|`, silently
  dropping the signal-agreement idea. I overrode it to fold in the `agreement` term and to
  force-route sub-40-word text to `uncertain` with a capped confidence — neither of which it
  inferred on its own. I verified the corrected version against the four calibration inputs before
  wiring it into the endpoint.

---

## Project layout

```
app.py            Flask API: /submit, /appeal, /log, /appeals, /health + rate limiting
signals.py        Signal 1 (Groq LLM + offline fallback) and Signal 2 (stylometric)
scoring.py        Weighted combination, confidence formula, attribution bands
labels.py         The three transparency-label variants
storage.py        SQLite: submissions (state) + audit_log (append-only events)
test_scoring.py   Calibration harness for the four spec inputs
planning.md       Design spec, architecture diagram, AI tool plan
```
