"""
Docker Runner — Execution & Self-Healing Build Loop

Clones a repository into a temporary sandbox, writes the AI-generated
Dockerfile, then attempts to build a Docker image up to MAX_RETRIES times.
On failure, the LLM is asked to fix the Dockerfile before the next attempt.
"""

import logging
import os
import shutil
import subprocess
import uuid

from services.llm_client import fix_infrastructure

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

# Base directory for temporary build sandboxes
SESSIONS_DIR = (
    r"C:\temp\reporun-sessions"
    if os.name == "nt"
    else "/tmp/reporun-sessions"
)


def execute_build_loop(
    repo_url: str,
    initial_docker_config: dict,
    file_tree: list[str],
) -> dict:
    """
    Execute the full clone → build → self-heal loop.

    Returns:
        {
            "status": "success" | "failed",
            "image_tag": str,
            "final_dockerfile": str,
            "attempts": int,
            "heal_history": [...],
            "last_error": str | None,
        }
    """
    session_id = uuid.uuid4().hex[:12]
    sandbox_dir = os.path.join(SESSIONS_DIR, session_id)
    image_tag = f"reporun-{session_id}"
    dockerfile_path = os.path.join(sandbox_dir, "Dockerfile")

    current_dockerfile = initial_docker_config["dockerfile"]
    heal_history: list[dict] = []

    try:
        # ── Ensure sandbox directory exists ──
        os.makedirs(sandbox_dir, exist_ok=True)

        # ── Clone the repository ──
        logger.info("Cloning repository: %s → %s", repo_url, sandbox_dir)
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, "."],
            cwd=sandbox_dir,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        logger.info("Clone complete (session=%s)", session_id)

        # ── Write initial Dockerfile ──
        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(current_dockerfile)

        # ── Build loop with self-healing ──
        for attempt in range(1, MAX_RETRIES + 1):
            logger.info("Build attempt %d/%d (session=%s)", attempt, MAX_RETRIES, session_id)

            result = subprocess.run(
                ["docker", "build", "-t", image_tag, "."],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                # ── Build succeeded ──
                logger.info("Docker build succeeded (session=%s, attempt=%d)", session_id, attempt)
                return {
                    "status": "success",
                    "image_tag": image_tag,
                    "final_dockerfile": current_dockerfile,
                    "attempts": attempt,
                    "heal_history": heal_history,
                    "last_error": None,
                }

            # ── Build failed ──
            stderr = (result.stderr or result.stdout or "Unknown build error")[-3000:]
            logger.warning(
                "Build attempt %d failed (session=%s): %s",
                attempt, session_id, stderr[:500],
            )

            # If this was the last attempt, give up
            if attempt == MAX_RETRIES:
                logger.error("All build attempts exhausted (session=%s)", session_id)
                return {
                    "status": "failed",
                    "image_tag": image_tag,
                    "final_dockerfile": current_dockerfile,
                    "attempts": attempt,
                    "heal_history": heal_history,
                    "last_error": stderr,
                }

            # ── Self-heal: ask the LLM to fix the Dockerfile ──
            logger.info("Requesting LLM self-heal (session=%s, attempt=%d)", session_id, attempt)
            fix = fix_infrastructure(stderr, current_dockerfile, file_tree)

            heal_history.append({
                "attempt": attempt,
                "reasoning": fix.get("reasoning", ""),
                "error_snippet": stderr[:300],
            })

            # Update the Dockerfile for the next attempt
            current_dockerfile = fix["dockerfile"]
            with open(dockerfile_path, "w", encoding="utf-8") as f:
                f.write(current_dockerfile)
            logger.info("Dockerfile updated by self-heal (session=%s, attempt=%d)", session_id, attempt)

    finally:
        # ── Cleanup: remove the sandbox directory ──
        try:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
            logger.info("Sandbox cleaned up (session=%s)", session_id)
        except Exception as cleanup_err:
            logger.warning("Sandbox cleanup failed (session=%s): %s", session_id, cleanup_err)
