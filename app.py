"""Provenance Guard — Flask API.

Endpoints:
  POST /submit   classify a piece of text; returns attribution, confidence, label.
  POST /appeal   contest a classification; flips status to under_review and logs it.
  GET  /log      recent audit-log events (for documentation / grading visibility).
  GET  /appeals  reviewer queue of submissions currently under_review.
  GET  /health   liveness + whether the Groq key is configured.
"""

import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()  # load GROQ_API_KEY from .env before signals import-time reads it

import storage
from labels import build_label
from scoring import score_text

app = Flask(__name__)
storage.init_db()

# In-memory rate limiting is fine for local dev / this prototype.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.route("/health", methods=["GET"])
def health():
    import os
    key = os.environ.get("GROQ_API_KEY", "").strip()
    return jsonify({
        "status": "ok",
        "groq_key_configured": bool(key) and key != "your_key_here",
    })


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    creator_id = (body.get("creator_id") or "").strip()

    if not text:
        return jsonify({"error": "Field 'text' is required and cannot be empty."}), 400
    if not creator_id:
        return jsonify({"error": "Field 'creator_id' is required."}), 400

    result = score_text(text)
    content_id = str(uuid.uuid4())
    label = build_label(result["attribution"], result["confidence"])

    result_with_label = {**result, "label": label}
    timestamp = storage.record_classification(content_id, creator_id, text, result_with_label)

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": result["attribution"],
        "ai_probability": result["ai_probability"],
        "confidence": result["confidence"],
        "label": label,
        "signals": {
            "llm_score": result["llm_score"],
            "llm_rationale": result["llm_rationale"],
            "llm_available": result["llm_available"],
            "stylometric_score": result["stylometric_score"],
            "stylometric_metrics": result["stylometric_metrics"],
        },
        "word_count": result["word_count"],
        "status": "classified",
        "timestamp": timestamp,
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = (body.get("content_id") or "").strip()
    reasoning = (body.get("creator_reasoning") or "").strip()

    if not content_id:
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not reasoning:
        return jsonify({"error": "Field 'creator_reasoning' is required."}), 400

    updated = storage.record_appeal(content_id, reasoning)
    if updated is None:
        return jsonify({"error": f"No submission found with content_id '{content_id}'."}), 404

    return jsonify({
        "message": "Appeal received. This content is now under review by a human moderator.",
        "content_id": content_id,
        "status": updated["status"],
        "original_attribution": updated["attribution"],
        "original_confidence": updated["confidence"],
        "creator_reasoning": reasoning,
    })


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": storage.get_log(limit=limit)})


@app.route("/appeals", methods=["GET"])
def appeals():
    return jsonify({"queue": storage.get_appeals()})


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1") != "0"
    app.run(host="0.0.0.0", port=port, debug=debug)
