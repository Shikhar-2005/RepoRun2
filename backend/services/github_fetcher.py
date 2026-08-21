"""
GitHub Fetcher Service

Extracts owner/repo from a GitHub URL, then uses the GitHub REST API
to fetch the repository file tree (recursive) and README.md content.
"""

import os
import re
import requests

GITHUB_API = "https://api.github.com"


def _parse_github_url(url: str) -> tuple[str, str]:
    """
    Parse a standard GitHub URL into (owner, repo).
    Accepts formats like:
      https://github.com/owner/repo
      https://github.com/owner/repo.git
      https://github.com/owner/repo/tree/main/...
    """
    pattern = r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)"
    match = re.match(pattern, url.strip())

    if not match:
        raise ValueError("Invalid GitHub URL. Expected format: https://github.com/owner/repo")

    owner = match.group(1)
    repo = match.group(2).removesuffix(".git")
    return owner, repo


def _get_headers() -> dict:
    """Build common headers for GitHub API requests."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "RepoRun/2.0",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_file_tree(owner: str, repo: str) -> list[str]:
    """
    Fetch the recursive file tree for the default branch of a repository.
    Returns a flat list of file paths (blobs only, no tree entries).
    """
    # Get the default branch
    repo_res = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}",
        headers=_get_headers(),
        timeout=15,
    )
    repo_res.raise_for_status()
    default_branch = repo_res.json()["default_branch"]

    # Fetch the recursive git tree
    tree_res = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1",
        headers=_get_headers(),
        timeout=15,
    )
    tree_res.raise_for_status()

    return [
        item["path"]
        for item in tree_res.json()["tree"]
        if item["type"] == "blob"
    ]


def _fetch_readme(owner: str, repo: str) -> str:
    """
    Fetch the raw content of README.md from the repository.
    Returns the text content, or a fallback message if not found.
    """
    try:
        res = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/readme",
            headers={**_get_headers(), "Accept": "application/vnd.github.v3.raw"},
            timeout=15,
        )
        res.raise_for_status()
        return res.text
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return "(No README.md found in this repository)"
        raise


def get_repo_context(url: str) -> dict:
    """
    Main entry point.
    Takes a GitHub URL and returns {"file_tree": [...], "readme_content": "..."}.
    """
    owner, repo = _parse_github_url(url)

    try:
        file_tree = _fetch_file_tree(owner, repo)
        readme_content = _fetch_readme(owner, repo)
        return {"file_tree": file_tree, "readme_content": readme_content}
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 404:
            raise RuntimeError(
                f"Repository not found: {owner}/{repo}. It may be private or misspelled."
            ) from e
        if status == 403:
            raise RuntimeError(
                "GitHub API rate limit exceeded. Set a GITHUB_TOKEN in your .env to increase limits."
            ) from e
        raise RuntimeError(f"Failed to fetch repository context: {e}") from e
