# MiniOJ

English | [简体中文](README_zh.md)

MiniOJ is a small multi-user online judge designed for both browser users and coding agents. It runs on WSL Ubuntu, keeps application state in SQLite, and executes untrusted C++20 programs only inside restricted Docker containers.

## What is included

- Registration, login, signed browser sessions, profile and password management
- `user` and `admin` roles with server-side authorization
- One-time-display Agent API tokens; only SHA-256 token digests are stored
- Problem statements, public samples, and file-backed hidden testcases
- Browser editor, custom runs, submissions, histories, and detailed results
- Versioned REST API with agent-safe problem data and structured judge feedback
- A separate database-polling judge worker
- Docker restrictions for network, CPU, memory, PIDs, capabilities, user, filesystem, time, and output
- C++20 judging with AC, WA, CE, RE, TLE, basic MLE, OLE, and IE verdicts

V1 intentionally has no contests, rankings, OAuth, special judges, interactive problems, Redis, or multiple languages.

## Architecture

```text
Browser ── session cookie ─┐
                          ├── FastAPI server ── SQLite
Agent ─── Bearer token ───┘          │
                                     │ QUEUED submissions
                              independent worker
                                     │
                              restricted Docker
                                     │
                                  C++20 code
```

The server never executes user binaries. The worker invokes Docker, and only the per-job directory is mounted into a user-code container. The Docker socket, database, WSL home, and complete testcase directory are not mounted into that container.

## WSL Ubuntu setup

Prerequisites:

- Conda or Miniconda (the environment pins Python 3.12)
- Docker Engine or Docker Desktop with WSL integration
- A working `docker` command available to the Linux user running the worker

From this repository:

```bash
conda env create --file environment.yml
conda activate minioj
cp .env.example .env
```

To synchronize an existing environment after dependency changes:

```bash
conda env update --file environment.yml --prune
```

Export the settings in `.env` through your preferred environment loader, and replace `MINIOJ_SECRET_KEY` before exposing the service. A convenient shell-only development setup is:

```bash
export MINIOJ_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
docker build -t minioj-cpp20:latest docker/cpp20
minioj init-db
minioj create-admin
```

Start the HTTP server and worker in separate terminals:

```bash
conda activate minioj
uvicorn minioj.server.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
conda activate minioj
minioj-worker
```

The worker stays in the foreground and polls for submissions every second. `Worker ready` followed by `No queued submissions; waiting for new submissions.` means it is ready and idle. Keep this terminal open while submitting code in the browser; press `Ctrl+C` to stop. Use `minioj-worker --once` to process queued submissions until the queue is empty and then exit. Startup logs show database initialization and the Docker judge image check.

Open <http://localhost:8000>. FastAPI's interactive API documentation is at <http://localhost:8000/docs>.

For the Tailscale-facing Nginx deployment, create `.env` from `.env.example`, set a strong `MINIOJ_SECRET_KEY`, and run:

```bash
docker compose up -d --build
```

Open <http://100.95.57.121/minioj/>. Nginx is the only published service; it proxies the `/minioj/` prefix to the internal FastAPI container. Set `MINIOJ_HTTP_BIND` in `.env` if the host address changes.

Run the judge worker directly in WSL so Docker bind mounts resolve to the same host paths. Production deployments should give the worker its own tightly controlled Docker daemon rather than mounting a general-purpose daemon into the web server.

For a Compose deployment, create the administrator in the running server so it uses the same persistent database as the website:

```bash
docker compose exec server minioj create-admin --username admin
```

Enter an email and a password of at least 10 characters, then sign in at <http://100.95.57.121/minioj/login>. Public registration creates standard users only; `admin` (case-insensitive) and the configured bootstrap administrator name are reserved. The CLI prints the database used. The `./database:/app/database` mount preserves accounts across container rebuilds.

Compose also forwards the three optional `MINIOJ_ADMIN_USERNAME`, `MINIOJ_ADMIN_EMAIL`, and `MINIOJ_ADMIN_PASSWORD` settings from `.env`. Set all three together to bootstrap an administrator. An existing matching active administrator is left unchanged; conflicting accounts cause a clear startup error rather than a silent skip or automatic promotion.

## First problem

1. Log in with the administrator account.
2. Open **Admin → New problem**.
3. Create the statement and limits.
4. Add at least one testcase on the edit page. `sample` testcases are public; `hidden` testcases are stored only under `data/problems/<problem-id>/tests/`.

A problem without testcases is accepted by the UI but its submissions receive `IE`, which makes incomplete judge data visible rather than silently accepting code.

## Agent API quick start

Create a token under **Settings → API tokens**, then save the displayed secret. It cannot be recovered later.

```bash
export OJ_TOKEN='oj_replace_me'

curl -H "Authorization: Bearer $OJ_TOKEN" \
  http://localhost:8000/api/v1/agent/problems/two-sum

curl -X POST http://localhost:8000/api/v1/submissions \
  -H "Authorization: Bearer $OJ_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"problem_id":"two-sum","language":"cpp20","source_code":"#include <iostream>\nint main(){return 0;}"}'

curl -H "Authorization: Bearer $OJ_TOKEN" \
  http://localhost:8000/api/v1/submissions/sub_replace_me

curl -H "Authorization: Bearer $OJ_TOKEN" \
  http://localhost:8000/api/v1/agent/submissions/sub_replace_me/feedback
```

Agent problem responses omit rating, tags, editorials, historical solutions, and hidden tests. Feedback exposure is controlled with `MINIOJ_FEEDBACK_POLICY=full|diagnostic|verdict_only`.

## Main endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Register a user |
| `GET` | `/api/v1/me` | Current identity |
| `GET/POST` | `/api/v1/tokens` | List or create own tokens |
| `DELETE` | `/api/v1/tokens/{id}` | Revoke an own token |
| `GET` | `/api/v1/problems` | Public problem list |
| `GET` | `/api/v1/problems/{id}` | Public problem detail |
| `GET` | `/api/v1/agent/problems/{id}` | Sanitized agent input |
| `POST` | `/api/v1/runs` | Synchronous custom run |
| `POST` | `/api/v1/submissions` | Queue a submission (`202`) |
| `GET` | `/api/v1/submissions/{id}` | Stable status/result fields |
| `GET` | `/api/v1/agent/submissions/{id}/feedback` | Structured diagnostic feedback |

Cookie-authenticated API mutations require the `X-CSRF-Token` header used by the bundled browser UI. Bearer-token requests do not.

## Data and operations

- SQLite: `database/oj.db`
- Testcases: `data/problems/<problem-id>/tests/`
- Ephemeral job files: `data/jobs/` (removed after every run)
- Default maximum source/input size: 256 KiB
- Default combined stdout/stderr limit: 1 MiB
- Default token lifetime: 90 days

Only one worker should normally be used for V1. Claiming is conditional and safe against two workers selecting the same queued row, but SQLite and host capacity remain the intended scaling boundary.

Run the checks with:

```bash
pytest
```

The automated suite does not require Docker and does not execute untrusted binaries. Perform one manual end-to-end Docker submission before deployment.

## Security notes

- Set a strong, persistent `MINIOJ_SECRET_KEY`; changing it invalidates browser sessions.
- Put the server behind HTTPS and set `MINIOJ_SESSION_HTTPS_ONLY=true` outside local development.
- Do not expose Docker's unauthenticated TCP socket.
- Use a dedicated host/VM for adversarial workloads. Docker isolation reduces risk but is not a substitute for a hardened kernel sandbox for hostile public judging.
- Back up the SQLite database and `data/problems` together so testcase metadata and files remain consistent.

