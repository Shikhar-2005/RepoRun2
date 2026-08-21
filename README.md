# RepoRun - Autonomous Infrastructure Agent

RepoRun is an AI-powered deployment engine that autonomously analyzes any public GitHub repository, determines its technology stack, generates a production-ready Dockerfile, builds a Docker image, and self-heals build failures -- all without human intervention.

Paste a GitHub URL into the web interface, click a single button, and watch the agent work. It reads your codebase, writes your infrastructure code, and fixes its own mistakes.

---

## Table of Contents

- [The Problem](#the-problem)
- [How RepoRun Solves It](#how-reporun-solves-it)
- [System Architecture](#system-architecture)
- [The AI Self-Healing Loop](#the-ai-self-healing-loop)
- [Prerequisites](#prerequisites)
- [Local Setup](#local-setup)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [License](#license)

---

## The Problem

Setting up a new repository locally is one of the most persistent sources of friction in software development. A developer clones a project and is immediately confronted with a series of questions: What runtime does this need? Which package manager? Are there system-level dependencies? What ports need to be exposed? What environment variables are required?

The typical workflow involves reading through a README (if one exists), cross-referencing configuration files, installing dependencies by trial and error, debugging cryptic build failures, and repeating the cycle until the application finally runs. This process can take anywhere from fifteen minutes to several hours, depending on the complexity of the stack.

For teams evaluating open-source tools, onboarding new engineers, or running code reviews across multiple repositories, this friction compounds rapidly. Every repository becomes a small research project before any productive work can begin.

---

## How RepoRun Solves It

RepoRun replaces the entire manual setup process with a single autonomous agent. Instead of reading documentation and writing configuration files by hand, the agent performs the following sequence:

1. It fetches the complete file tree and README content from the GitHub API.
2. It sends this structural context to a large language model (OpenAI GPT-4o-mini) with a carefully engineered system prompt.
3. The LLM analyzes the repository, identifies the primary language, framework, and package manager, and generates a complete Dockerfile along with metadata about required environment variables.
4. The agent clones the repository into a temporary sandbox directory.
5. It writes the generated Dockerfile into the sandbox and executes `docker build`.
6. If the build fails, the agent captures the error output and sends it back to the LLM for diagnosis and correction -- automatically retrying up to three times.
7. The final result, including the generated Dockerfile, detected stack, environment variables, and build status, is returned to the user through a web interface.

The entire process is hands-free. The user provides a URL. The agent does the rest.

---

## System Architecture

RepoRun follows a linear pipeline architecture with a feedback loop for error correction. Each stage is implemented as a discrete Python module.

```
+------------------+       +-------------------+       +-------------------+
|                  |       |                   |       |                   |
|   Frontend       | ----> |   Flask API       | ----> |   GitHub Fetcher  |
|   (HTML/JS)      |  POST |   (app.py)        |       |   (github_        |
|                  |  /api |                   |       |    fetcher.py)    |
|                  | /depl |                   |       |                   |
+------------------+  oy   +-------------------+       +-------------------+
                                    |                          |
                                    v                          v
                          +-------------------+       File tree + README
                          |                   |              |
                          |   Docker Runner   |              v
                          |   (docker_        |       +-------------------+
                          |    runner.py)      |       |                   |
                          |                   |       |   LLM Client      |
                          |   Clone -> Build  | <---- |   (llm_client.py) |
                          |   -> Self-Heal    |       |                   |
                          |                   |       |   generate_       |
                          +-------------------+       |   infrastructure  |
                                |     ^               |                   |
                                |     |               |   fix_            |
                                v     |               |   infrastructure  |
                          Build fails?|               |                   |
                          stderr -----+               +-------------------+
                          (fed back to LLM
                           for correction)
```

### Component Breakdown

**Frontend (frontend/index.html):**
A single-page web interface built with vanilla HTML, CSS, and JavaScript. It provides a repository URL input, a deploy button, a real-time status log panel, and a results display that renders the detected stack, environment variables, generated Dockerfile (with copy-to-clipboard), self-heal history, and build status.

**Flask API (backend/app.py):**
A minimal Flask server with CORS enabled. It serves the frontend as static files and exposes two endpoints: a health check at `GET /api/health` and the main pipeline at `POST /api/deploy`. The deploy endpoint orchestrates the three services in sequence and returns a unified JSON response.

**GitHub Fetcher (backend/services/github_fetcher.py):**
Parses the GitHub URL to extract the owner and repository name. Uses the GitHub REST API to fetch the repository metadata (to determine the default branch), the recursive Git tree (to produce a flat list of all file paths), and the raw README content. Supports optional authentication via `GITHUB_TOKEN` for higher rate limits.

**LLM Client (backend/services/llm_client.py):**
Interfaces with the OpenAI API using the official Python SDK. Provides two functions:
- `generate_infrastructure`: Sends the file tree and README to GPT-4o-mini with a system prompt instructing it to analyze the stack and produce a Dockerfile, environment variable list, and build explanation. Uses `response_format={"type": "json_object"}` to guarantee parseable output.
- `fix_infrastructure`: Sends a failed build's stderr, the broken Dockerfile, and the file tree to the LLM with a diagnostic system prompt. The LLM returns a corrected Dockerfile with reasoning.

**Docker Runner (backend/services/docker_runner.py):**
Manages the execution sandbox. Generates a unique session ID, creates a temporary directory, clones the repository using `git clone --depth 1`, writes the Dockerfile, and runs `docker build`. Implements the retry loop with self-healing (detailed below). Cleans up the sandbox directory on exit regardless of outcome.

---

## The AI Self-Healing Loop

The self-healing loop is the core differentiating feature of RepoRun. It transforms a fragile, one-shot code generation process into a resilient, iterative agent.

### How It Works

When the Docker Runner executes `docker build` and the build fails (non-zero return code), the following sequence occurs:

1. **Error Capture:** The agent captures the complete stderr output from the failed build process. This typically contains compiler errors, missing dependency messages, incorrect file paths, or version conflicts.

2. **Diagnostic Prompt:** The stderr, the current Dockerfile content, and the repository file tree are assembled into a structured prompt and sent to the LLM. The system prompt instructs the model to act as a DevOps engineer diagnosing a failed build. It must identify the root cause -- whether that is a missing system package, an incorrect COPY path, a version mismatch, or a misconfigured build step -- and produce a corrected Dockerfile.

3. **Dockerfile Replacement:** The agent receives the corrected Dockerfile from the LLM, overwrites the file in the sandbox directory, and records the fix reasoning and error snippet in a heal history array.

4. **Retry:** The build loop advances to the next attempt and executes `docker build` again with the corrected Dockerfile.

5. **Termination:** This cycle repeats for a maximum of three attempts. If the build succeeds on any attempt, the agent returns a success status along with the final Dockerfile and the complete heal history. If all three attempts fail, the agent returns a failure status with the last error log so the user can diagnose manually.

### Why Three Attempts

Most Docker build failures caused by LLM-generated Dockerfiles fall into a small number of categories: missing system dependencies (e.g., `libpq-dev` for PostgreSQL bindings), incorrect file paths in COPY instructions, or base image version incompatibilities. These are typically resolved within one or two correction cycles. Three attempts provides sufficient room for iterative fixes while preventing runaway API calls on fundamentally unbuildable repositories.

### Observability

Every self-heal cycle is recorded and returned to the frontend. The UI displays a timeline showing each failed attempt, the error snippet that triggered the heal, and the LLM's reasoning for its fix. This gives the user full transparency into the agent's decision-making process.

---

## Prerequisites

Before running RepoRun locally, ensure the following are installed on your system:

- **Python 3.10 or later** -- Required for the Flask backend and all service modules.
- **Docker Desktop** -- Must be installed and running. The agent executes `docker build` via subprocess, so the Docker CLI must be available in your system PATH.
- **Git** -- Required for cloning repositories into the build sandbox. Must be available in your system PATH.
- **An OpenAI API key** -- Required for the LLM-powered analysis and self-healing. You can obtain one from https://platform.openai.com/api-keys.
- **A GitHub Personal Access Token (optional)** -- Recommended if you expect to analyze many repositories in a short period, as unauthenticated GitHub API requests are limited to 60 per hour.

---

## Local Setup

Follow these steps to run RepoRun on your local machine.

### 1. Clone the Repository

```bash
git clone https://github.com/Shikhar-2005/RepoRun2.git
cd RepoRun2
```

### 2. Create a Python Virtual Environment

```bash
cd backend
python -m venv venv
```

Activate the virtual environment:

On macOS/Linux:
```bash
source venv/bin/activate
```

On Windows:
```bash
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the `backend/` directory (or edit the existing one):

```
PORT=5000
HOST=0.0.0.0
OPENAI_API_KEY=sk-your-openai-api-key-here
GITHUB_TOKEN=ghp-your-github-token-here
```

The `OPENAI_API_KEY` is required. The `GITHUB_TOKEN` is optional but recommended.

### 5. Ensure Docker is Running

Verify that Docker Desktop is running and the CLI is accessible:

```bash
docker info
```

If this command returns system information without errors, Docker is ready.

### 6. Start the Server

```bash
python app.py
```

The server will start on `http://localhost:5000` by default. Open this URL in your browser to access the web interface.

### 7. Use the Application

1. Paste a public GitHub repository URL into the input field (e.g., `https://github.com/expressjs/express`).
2. Click the "Deploy with AI" button.
3. The status log will display real-time progress: fetching context, generating infrastructure, cloning, building.
4. When complete, the results panel will show the detected stack, required environment variables, the generated Dockerfile, and the build status.

---

## Project Structure

```
RepoRun2/
|-- .gitignore
|-- README.md
|-- backend/
|   |-- .env                        # Environment variables (not committed)
|   |-- app.py                      # Flask server and API routes
|   |-- requirements.txt            # Python dependencies
|   |-- services/
|   |   |-- __init__.py
|   |   |-- github_fetcher.py       # GitHub API integration
|   |   |-- llm_client.py           # OpenAI LLM integration
|   |   |-- docker_runner.py        # Build execution and self-healing loop
|-- frontend/
|   |-- index.html                  # Single-page web interface
```

---

## API Reference

### GET /api/health

Returns the server status.

**Response:**
```json
{
  "status": "ok",
  "version": "2.0.0",
  "uptime": 123.45
}
```

### POST /api/deploy

Triggers the full analysis, generation, and build pipeline.

**Request Body:**
```json
{
  "repoUrl": "https://github.com/owner/repo"
}
```

**Success Response (build passed):**
```json
{
  "status": "success",
  "data": {
    "detected_stack": "Node.js / Express",
    "required_envs": ["PORT", "DATABASE_URL"],
    "build_command_explanation": "...",
    "dockerfile": "FROM node:18-alpine\n...",
    "imageTag": "reporun-a1b2c3d4e5f6",
    "attempts": 1,
    "healHistory": [],
    "lastError": null
  }
}
```

**Failure Response (build failed after retries):**
```json
{
  "status": "failed",
  "data": {
    "detected_stack": "Python / Django",
    "dockerfile": "FROM python:3.11-slim\n...",
    "attempts": 3,
    "healHistory": [
      {
        "attempt": 1,
        "reasoning": "Added libpq-dev for psycopg2 compilation",
        "errorSnippet": "Error: pg_config executable not found..."
      }
    ],
    "lastError": "full stderr output..."
  }
}
```

---

## Configuration

All configuration is managed through environment variables in `backend/.env`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | -- | Your OpenAI API key for GPT-4o-mini access |
| `GITHUB_TOKEN` | No | -- | GitHub personal access token for higher API rate limits |
| `PORT` | No | 5000 | Port the Flask server listens on |
| `HOST` | No | 0.0.0.0 | Host address the Flask server binds to |

---

## License

This project is licensed under the MIT License.
