"""
LLM Client Service

Uses the OpenAI API (or compatible) to analyze a repository's file tree
and README, then generates a production-ready Dockerfile and deployment metadata.
Also provides a self-healing function that fixes failed Dockerfiles.
"""

import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> OpenAI:
    """
    Lazy-initialize the OpenAI client so the server can start
    even when the API key isn't set yet. The missing-key error
    will surface at request time instead of at import time.
    """
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to your backend/.env file to enable AI features."
            )
        _client = OpenAI(api_key=api_key)
    return _client


SYSTEM_PROMPT = (
    "You are an expert DevOps engineer and Autonomous Infrastructure Agent. "
    "Your task is to analyze a repository's file tree and README.md content to determine "
    "its technology stack, and then generate a production-ready, secure Dockerfile to run the application.\n\n"
    "INPUT CONTEXT:\n\n"
    "FILE_TREE: A structural mapping of the repository.\n\n"
    "README_CONTENT: The raw text of the repository's README file.\n\n"
    "YOUR INSTRUCTIONS:\n\n"
    "Analyze the Stack: Identify the primary language, framework, and package manager.\n\n"
    "Generate the Dockerfile: Write an optimized, single-stage Dockerfile. "
    "Use lightweight base images (like alpine or -slim).\n\n"
    "Identify Secrets: Scan for required environment variables.\n\n"
    "Expose Ports: Include the EXPOSE instruction based on the framework.\n\n"
    "OUTPUT FORMAT:\n"
    "You must respond STRICTLY with a valid JSON object matching this schema:\n"
    "{\n"
    '"detected_stack": "string",\n'
    '"dockerfile": "string (raw dockerfile content with \\n)",\n'
    '"required_envs": ["array", "of", "strings"],\n'
    '"build_command_explanation": "string"\n'
    "}"
)

SELF_HEAL_PROMPT = (
    "You are an expert DevOps AI diagnosing a failed Docker build.\n\n"
    "INPUT CONTEXT:\n\n"
    "ERROR_LOG: The stderr output from the failed 'docker build' command.\n\n"
    "CURRENT_DOCKERFILE: The Dockerfile that caused the error.\n\n"
    "FILE_TREE: The repository structure.\n\n"
    "YOUR INSTRUCTIONS:\n"
    "Analyze the error log. Identify the missing system dependency, incorrect path, "
    "or version conflict. Generate a FIXED Dockerfile.\n\n"
    "OUTPUT FORMAT:\n"
    "Respond STRICTLY with a JSON object matching this schema:\n"
    "{\n"
    '"reasoning": "Brief explanation of what went wrong and how you are fixing it",\n'
    '"dockerfile": "string (the complete, fixed Dockerfile content)"\n'
    "}"
)


def generate_infrastructure(file_tree: list[str], readme_content: str) -> dict:
    """
    Analyze a repository's context via the LLM and return structured infrastructure output.

    Returns a dict with: detected_stack, dockerfile, required_envs, build_command_explanation
    """
    user_message = f"FILE_TREE:\n{chr(10).join(file_tree)}\n\nREADME_CONTENT:\n{readme_content}"

    logger.info(
        "Calling LLM for infrastructure generation (files=%d, readme_len=%d)",
        len(file_tree),
        len(readme_content),
    )

    completion = _get_client().chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    raw = completion.choices[0].message.content

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("LLM returned non-JSON response: %s", raw[:200])
        raise RuntimeError("LLM returned an unparseable response. Please try again.")

    # Validate expected fields
    required_fields = ["detected_stack", "dockerfile", "required_envs", "build_command_explanation"]
    for field in required_fields:
        if field not in result:
            raise RuntimeError(f'LLM response missing required field: "{field}"')

    logger.info(
        "Infrastructure generated successfully (stack=%s, envs=%d)",
        result["detected_stack"],
        len(result["required_envs"]),
    )

    return result


def fix_infrastructure(error_log: str, current_dockerfile: str, file_tree: list[str]) -> dict:
    """
    Self-healing: takes a failed build's error log, the broken Dockerfile,
    and the repo file tree, then asks the LLM to produce a fixed Dockerfile.

    Returns a dict with: reasoning, dockerfile
    """
    user_message = (
        f"ERROR_LOG:\n{error_log}\n\n"
        f"CURRENT_DOCKERFILE:\n{current_dockerfile}\n\n"
        f"FILE_TREE:\n{chr(10).join(file_tree)}"
    )

    logger.info("Calling LLM for self-healing fix (error_len=%d)", len(error_log))

    completion = _get_client().chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": SELF_HEAL_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    raw = completion.choices[0].message.content

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("LLM self-heal returned non-JSON response: %s", raw[:200])
        raise RuntimeError("LLM returned an unparseable self-heal response.")

    if "dockerfile" not in result:
        raise RuntimeError('LLM self-heal response missing "dockerfile" field.')

    logger.info("Self-heal fix generated (reasoning=%s)", result.get("reasoning", "")[:100])
    return result
