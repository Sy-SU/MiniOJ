# MiniOJ

[English](README.md) | [简体中文](README_zh.md)

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

Edit `.env` before startup. Replace `MINIOJ_SECRET_KEY` with a persistent random value of at least 32 characters; the server refuses the example placeholder and short values. You can generate a value to paste into `.env` with:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

MiniOJ reads process environment variables and does not parse `.env` itself. Docker Compose automatically reads `.env` for the values explicitly mapped in `compose.yaml`. For host commands such as `minioj`, `uvicorn`, and `minioj-worker`, export the file in every new terminal first:

```bash
set -a
. ./.env
set +a
```

Then initialize the application:

```bash
docker build -t minioj-cpp20:latest docker/cpp20
minioj init-db
minioj create-admin
```

Start the HTTP server and worker in separate terminals:

```bash
conda activate minioj
set -a
. ./.env
set +a
uvicorn minioj.server.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
conda activate minioj
set -a
. ./.env
set +a
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
3. Create the statement and limits. Problem IDs are 3–80 ASCII letters, numbers, or hyphens and may preserve mixed case. Statement fields support safe CommonMark plus KaTeX math: use `$...$` for inline formulas and a standalone `$$...$$` block for display formulas. Use **Preview Markdown** before saving. Embedded HTML is displayed as text and unsafe link schemes are not activated. KaTeX is bundled locally, so rendering does not require a browser CDN connection.
4. In **02 · Standard solution**, paste C++20 source and click **Save standard solution**, or upload a UTF-8 source file. The saved source is shown directly in the editor and can be edited there; it remains Admin-only. Save the standard solution separately from the problem statement.
5. Upload a testcase input as `sample` or `hidden`; the Worker runs the standard solution and stores stdout as the expected output. Alternatively, upload a C++20 generator to build one or more `generated` testcases.

Generator runs receive the seed in `argv[1]` and the 1-based case index in `argv[2]`; stdout becomes testcase input and is then passed to the standard solution. Jobs are asynchronous, so keep `minioj-worker` running and refresh the editor to see `FINISHED` or `FAILED`. A failed compile/run creates no testcase; a generator batch is stored atomically.

Each generated input or output is limited by `MINIOJ_TESTCASE_FILE_LIMIT_BYTES` (16 MiB by default). MiniOJ records SHA-256 checksums for both. Standard-solution and generator source files use `MINIOJ_SOURCE_LIMIT_BYTES`.

After upgrading an existing installation, rebuild/restart the Web service and restart the host `minioj-worker`; startup applies the compatible database upgrade automatically. For the documented Compose deployment, run `./restart`, then restart the separately running Worker.

Administrators can edit a problem, its standard solution, and its testcases even after submissions exist. The problem page shows a modification warning; older submissions show a warning when their recorded revision differs from the current one. Existing results are retained without automatic rejudging. A queued submission whose problem changes before the Worker reads its data finishes with `IE` and an explanation; a running judge finishes using the data it already captured.

Warnings have a **×** button to dismiss them for the current page; refreshing shows them again. The editor's numbered sections and shortcuts separate the problem statement/limits, standard solution, testcase management, and build history. Input-file and generator builds have separate panels.

Deleting a problem removes it from listings and blocks new submissions. Its old URL shows a deletion notice, and historical submissions remain viewable with the same warning. Deletion is logical: the database row, testcase files, and history remain stored, and the ID cannot be reused. Pending generator jobs are cancelled; replacing the standard solution rejects results from jobs using the previous solution. Multiple queued input/generator jobs using the same solution can still append testcases normally.

After updating, restart both Web and Worker so they use the same lifecycle rules; startup adds the new revision/deletion columns automatically. For Compose, rebuild the Web image with the existing `./restart` workflow, then restart the host Worker. No database reset is needed.

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
  http://localhost:8000/api/v1/submissions/1

curl -H "Authorization: Bearer $OJ_TOKEN" \
  http://localhost:8000/api/v1/agent/submissions/1/feedback
```

Agent problem responses omit rating, tags, editorials, historical solutions, and hidden tests. Feedback exposure is controlled with `MINIOJ_FEEDBACK_POLICY=full|diagnostic|verdict_only`.

## Main endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Register a user |
| `GET` | `/api/v1/me` | Current identity |
| `GET/POST` | `/api/v1/tokens` | List or create own tokens |
| `DELETE` | `/api/v1/tokens/{id}` | Delete an own token (immediately invalidates it) |
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
- Ephemeral job files: `MINIOJ_JOB_DIR`, falling back to `MINIOJ_DATA_DIR/jobs`; each completed run removes its own directory
- Maximum size of each testcase input or output file: `MINIOJ_TESTCASE_FILE_LIMIT_BYTES` (default 16777216 bytes)
- Standard-solution/generator run limit: `MINIOJ_TESTCASE_BUILD_TIME_LIMIT_MS` (default 10000 ms) and `MINIOJ_TESTCASE_BUILD_MEMORY_MB` (default 512 MiB)
- Maximum cases in one generator job: `MINIOJ_GENERATOR_MAX_CASES` (default 50)
- Maximum source size: `MINIOJ_SOURCE_LIMIT_BYTES` (default 262144 bytes)
- Maximum Custom Run input size: `MINIOJ_STDIN_LIMIT_BYTES` (default 262144 bytes)
- Combined stdout/stderr limit: `MINIOJ_OUTPUT_LIMIT_BYTES` (default 1048576 bytes)
- Default token lifetime: `MINIOJ_TOKEN_DEFAULT_DAYS` (default 90 days)

`MINIOJ_JOB_DIR` is independently configurable so ephemeral compiler and runtime files can live outside testcase storage. Omitting it preserves the existing `data/jobs` layout. For a host-run Worker, `/tmp/minioj/jobs` is a suitable non-persistent choice. The directory used for a Docker bind mount must be visible to the Docker daemon; the current Compose Custom Run limitation described in [the architecture document](docs/architecture.md) still applies.

Only one worker should normally be used for V1. Submission and testcase-build claims use conditional updates, but SQLite and host capacity remain the intended scaling boundary. A Worker restart currently leaves already-RUNNING work for manual diagnosis; automatic recovery is still pending.

Run the fast, Docker-free checks with:

```bash
conda activate minioj
ruff format --check .
ruff check .
pytest
```

With Docker and the judge image available, run the isolated end-to-end checks:

```bash
python scripts/smoke_test_stack.py
python scripts/smoke_test_judge.py
python scripts/smoke_test_generator.py
```

The first script verifies a custom run plus the complete API → queue → worker → Docker → feedback flow against a temporary database. The second verifies AC, WA, CE, RE, TLE, MLE, and OLE. The third compiles a real C++ generator and standard solution and verifies two generated input/output pairs. The scripts remove their temporary jobs and data when complete; they do not use the configured production database.

The `pytest` suite does not require Docker and does not execute untrusted binaries. The two smoke-test scripts do require a running Docker daemon and the `minioj-cpp20:latest` image.

## Rebuild and restart

Run `./restart` from the project root after changing the source or Dockerfile. It detects the WSL networking mode: NAT uses the current IPv4 default gateway (the Windows host), while mirrored mode uses `127.0.0.1`; the default proxy port is `7897`. Older WSL installations without networking-mode information fall back to the NAT gateway. Override detection with `MINIOJ_RESTART_PROXY=http://HOST:PORT ./restart`. Keep the Windows proxy running and accessible from WSL.

The script exports uppercase and lowercase HTTP/HTTPS proxy variables, then checks the dedicated `minioj-builder` BuildKit container. When the proxy changes, it recreates this builder with `docker buildx rm --keep-state` and reuses its cache volume. It explicitly selects that builder for `docker compose build` and passes proxy build arguments to dependency installation. After a successful build it starts Compose with `--no-build --wait`, then restarts Nginx. Failed preparation/build steps stop before services are restarted. Do not run it concurrently with another build using `minioj-builder`.

The script does not modify `.env`, the calling shell, or Docker daemon settings. A cached BuildKit image can bootstrap the builder even when the daemon's old proxy is unavailable. On a fresh machine, pulling the BuildKit image (or using `docker pull` directly) still requires the Docker daemon to have a working proxy. See [Docker's cache persistence documentation](https://docs.docker.com/build/builders/drivers/docker-container/#cache-persistence).

## Account input limits

- New usernames contain 3–10 ASCII letters (`A–Z`, `a–z`), with surrounding whitespace removed. Administrator names remain reserved for public registration. Existing accounts can still log in with their original usernames.
- Email addresses are trimmed and lowercased, with at most 254 characters total and 64 before `@`. ASCII addresses with a dotted domain and `+` tags are supported; whitespace, control characters, display names, and quoted addresses are rejected.
- Passwords require at least 10 characters and at most 1024 UTF-8 bytes. Spaces, symbols, and Unicode are allowed without trimming or truncation. Registration and password changes require matching confirmation; login and current-password verification also reject oversized passwords.
- Account POST bodies are limited to 16 KiB (HTTP 413), including chunked requests without `Content-Length`. This limit does not apply to code submissions.
- Browser limits accompany server validation, parameterized database queries, and automatic HTML escaping.
- Newly generated API tokens have a **Copy** button in Settings, with manual selection available if clipboard access fails. The full token is still shown only once. Lists show its first 7 and last 4 characters separated by 8 asterisks, e.g. `oj_abcd********wxyz`. Only the hash and masked preview are stored in the database. Startup adds the preview column to existing databases; older tokens show an unavailable-preview label because their hashes cannot recover these characters, and remain usable and deletable. **Delete** permanently removes a token and immediately invalidates it; previously revoked tokens can also be deleted.

## Security notes

- Set a strong, persistent `MINIOJ_SECRET_KEY`; changing it invalidates browser sessions.
- Put the server behind HTTPS and set `MINIOJ_SESSION_HTTPS_ONLY=true` outside local development.
- Do not expose Docker's unauthenticated TCP socket.
- Use a dedicated host/VM for adversarial workloads. Docker isolation reduces risk but is not a substitute for a hardened kernel sandbox for hostile public judging.
- Back up the SQLite database and `data/problems` together so testcase metadata and files remain consistent.
