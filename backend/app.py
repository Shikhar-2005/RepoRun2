"""
RepoRun — AI-Powered Deployment Server (Flask)

Serves the frontend UI as static files and exposes
POST /api/deploy to orchestrate the full AI pipeline.
"""

import logging
import os
import sys
import time

from dotenv import load_dotenv

# Load .env before anything else reads env vars
load_dotenv()

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from services.github_fetcher import get_repo_context
from services.llm_client import generate_infrastructure
from services.docker_runner import execute_build_loop

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("reporun")

# ── Flask app ──
app = Flask(__name__, static_folder=None)
CORS(app)

# ── Frontend static files ──
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
FRONTEND_DIR = os.path.abspath(FRONTEND_DIR)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    """Serve the frontend SPA — fall back to index.html for unknown paths."""
    file_path = os.path.join(FRONTEND_DIR, path)
    if path and os.path.isfile(file_path):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, "index.html")


# ── Health check ──
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "version": "2.0.0",
        "uptime": time.process_time(),
    })


# ── Deploy endpoint ──
@app.route("/api/deploy", methods=["POST"])
def deploy():
    data = request.get_json(silent=True) or {}
    repo_url = data.get("repoUrl", "")

    if not repo_url or not isinstance(repo_url, str):
        return jsonify({
            "status": "error",
            "message": 'Missing or invalid "repoUrl" in request body.',
        }), 400

    try:
        # 1. Fetch repository context from GitHub
        logger.info("Fetching repo context: %s", repo_url)
        context = get_repo_context(repo_url)
        file_tree = context["file_tree"]
        readme_content = context["readme_content"]

        # 2. Generate infrastructure via LLM
        logger.info("Generating infrastructure (files=%d)", len(file_tree))
        ai_config = generate_infrastructure(file_tree, readme_content)

        # 3. Execute the build loop with self-healing
        logger.info("Starting build loop for: %s", repo_url)
        build_result = execute_build_loop(repo_url, ai_config, file_tree)

        # 4. Return the combined result
        status = "success" if build_result["status"] == "success" else "failed"
        return jsonify({
            "status": status,
            "data": {
                "detected_stack": ai_config["detected_stack"],
                "required_envs": ai_config["required_envs"],
                "build_command_explanation": ai_config["build_command_explanation"],
                "dockerfile": build_result["final_dockerfile"],
                "imageTag": build_result["image_tag"],
                "attempts": build_result["attempts"],
                "healHistory": [
                    {
                        "attempt": h["attempt"],
                        "reasoning": h["reasoning"],
                        "errorSnippet": h["error_snippet"],
                    }
                    for h in build_result["heal_history"]
                ],
                "lastError": build_result.get("last_error"),
            },
        })

    except Exception as e:
        logger.error("Deploy failed: %s", e)
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


# ── Entry point ──
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "0.0.0.0")
    logger.info("RepoRun server starting on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False)
