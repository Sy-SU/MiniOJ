# MiniOJ

[English](README.md) | [简体中文](README_zh.md)

MiniOJ is a small multi-user online judge designed for both browser users and coding agents. It runs on WSL Ubuntu, keeps application state in SQLite, and executes untrusted C++20 programs only inside restricted Docker containers.

Release candidate status (2026-10-02): **MiniOJ V1 source RC complete**. Core and optional CF tooling are committed/pushed separately; the isolated browser-fixture fix `40ede95` passed [hosted Checks CI](https://github.com/Sy-SU/MiniOJ/actions/runs/37008639747) with **725 tests, no failures or skips**, Ruff format/lint, application JS and example Compose checks. Production remains **READY_WITH_NOTES**, not deployed. Commit scopes, historical image differences and legacy backup-tracking notes are recorded in [TODO.md](TODO.md). No production database/service/image/tag was changed; any deployment or release tag needs separate approval.

## What is included

- Registration, login, signed browser sessions, profile and password management
- `user`, content `admin`, and `system` roles with centralized server-side authorization
- Independent management console, submission filters, single-submission rejudge and auditable Judge History
- Public contests, ordered existing problems, participation and dynamic ICPC standings
- Public user profiles, distinct current-AC solved lists and safe local avatar uploads
- One-time-display Agent API tokens; only SHA-256 token digests are stored
- Problem statements, public samples, and file-backed hidden testcases
- Admin file-upload import of complete Polygon ZIP packages, statement images, standard testlib and custom C++ checkers
- Browser editor with separate Run Sample, Custom Test, and Submit actions; histories and automatically updating results
- Versioned REST API with agent-safe problem data and structured judge feedback
- A separate database-polling judge worker
- Docker restrictions for network, CPU, memory, PIDs, capabilities, user, filesystem, time, and output
- C++20 judging with AC, WA, CE, RE, TLE, basic MLE, OLE, and IE verdicts

V1 has public contests, ICPC standings and single-contest Performance, but no permanent user rating, frozen standings, private contests, OAuth, scoring/interactive problems, Redis, or multiple languages. The Polygon extension supports C++ testlib special judges.

## Architecture

```text
Browser ── session cookie ─┐
                          ├── FastAPI server ── SQLite
Agent ─── Bearer token ───┘          │
                                     │ QUEUED run/build/submission jobs
                              independent worker
                                     │
                              restricted Docker
                                     │
                                  C++20 code
```

The server never executes user binaries. Custom Runs are queued internally while the HTTP request waits, so the same independent worker invokes Docker for every untrusted execution. Only the per-job directory is mounted into a user-code container. The Docker socket, database, WSL home, and complete testcase directory are not mounted into that container.

## WSL Ubuntu setup

Prerequisites:

- Conda or Miniconda (the environment pins Python 3.12)
- Docker Engine or Docker Desktop with WSL integration
- A working `docker` command available to the Linux user running the worker
- Docker cgroup v2 CPU accounting

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

Set the feedback mode in the OJ root `.env` with `MINIOJ_FEEDBACK_POLICY=full`. Supported values are `full` (detailed feedback within account permissions), `diagnostic` (safe, bounded compiler diagnostics and metadata without testcase contents), and `verdict_only` (verdict, safe summary and optional failed-test index). `GET /api/v1/me` reports the Server's loaded value as `feedback_mode`, which defaults to `full` and does not grant additional permissions. After editing `.env`, reload it and restart a host Server, or recreate the Compose Server container; `docker compose restart server` alone does not update its environment.

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

The worker stays in the foreground and polls Custom Runs, testcase builds, and submissions every second. `Worker ready` followed by `No queued work; waiting for Custom Runs, testcase builds, or submissions.` means it is ready and idle. Keep this terminal open while running or submitting code in the browser; press `Ctrl+C` to stop. Use `minioj-worker --once` to drain all three queues and exit. Startup takes an exclusive lock in `MINIOJ_JOB_DIR`, finalizes interrupted work without re-executing it, removes Worker-owned stale containers/job directories, and checks the Docker judge image. A second Worker using the same job directory is rejected.

For a persistent WSL user service, the supplied unit assumes this repository is at `%h/github-repos/MiniOJ` and Conda is at `%h/miniconda3`. Edit those two paths if needed, then install it:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/minioj-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now minioj-worker
systemctl --user status minioj-worker
journalctl --user -u minioj-worker -f
```

The unit loads the repository `.env`, restarts after failures, and uses the same database/data paths as foreground operation. WSL must have systemd enabled; `sudo loginctl enable-linger "$USER"` allows the user service to start without an interactive login. Docker must already be running and the user must have Docker access.

Open <http://localhost:8000>. FastAPI's interactive API documentation is at <http://localhost:8000/docs>.

For the Nginx deployment, create `.env` from `.env.example`, set a strong `MINIOJ_SECRET_KEY`, and run:

```bash
docker compose up -d --build
```

The public `.env.example` now sets `MINIOJ_HTTP_BIND=127.0.0.1`; after copying it, open <http://127.0.0.1/minioj/> locally. For remote/Tailscale access, explicitly set your own bind address and use it in the URL. Existing `.env` and the legacy Compose fallback are unchanged; always configure this variable explicitly. Nginx is the only published service; it proxies the `/minioj/` prefix to the internal FastAPI container. Set `MINIOJ_HTTP_PORT` if port 80 is unavailable. `MINIOJ_DATABASE_DIR` and `MINIOJ_DATA_HOST_DIR` can override the host bind-mount directories for isolated deployments; point the host Worker at those same paths.

Run the judge worker directly in WSL so Docker bind mounts resolve to the same host paths. Production deployments should give the worker its own tightly controlled Docker daemon rather than mounting a general-purpose daemon into the web server.

For a Compose deployment, create the administrator in the running server so it uses the same persistent database as the website:

```bash
docker compose exec server minioj create-admin --username admin
```

Enter an email and a password of at least 10 characters, then sign in at <http://127.0.0.1/minioj/login> (or your configured host). Public registration creates standard users only; `admin` (case-insensitive) and the configured bootstrap administrator name are reserved. The CLI prints the database used. The `./database:/app/database` mount preserves accounts across container rebuilds.

`create-admin` retains its compatible name but creates a `system` account. Compose forwards optional `MINIOJ_ADMIN_USERNAME`, `MINIOJ_ADMIN_EMAIL`, and `MINIOJ_ADMIN_PASSWORD` settings; set all three together to bootstrap a system administrator. An existing matching active system account is unchanged; conflicting accounts cause a clear error, not silent promotion. System users may assign the separate content-admin role in **管理 → Users**. Public registration always creates `user`.

## Upgrading an existing installation

This release adds a one-time legacy `admin` → `system` migration, avatar keys, nullable contest association, JudgeRun history, contest tables and nullable submission request hashes with a unique user/key index. Re-running `init-db` does not promote newly assigned content admins or change existing results. It backfills one initial JudgeRun per existing submission, without rejudging or deleting the database. Legacy submission request hashes remain null.

Do not mix old role/Worker code with the upgraded schema. In a planned maintenance window (these commands are instructions, not automatically executed):

1. Stop the host Worker (`systemctl --user stop minioj-worker` if using the provided unit; otherwise stop its terminal process) and stop Web traffic/processes (`docker compose stop nginx server` for Compose).
2. Back up the complete database directory, including any SQLite WAL/SHM files, the data directory and configuration while both writers are stopped. Keep the backup outside the repository; never use `down -v` or delete `oj.db` as an upgrade.
3. Install/synchronize updated host dependencies with `conda env update --file environment.yml --prune`. Activate `minioj` and load `.env` as above. Check that `MINIOJ_DATABASE_URL` and `MINIOJ_DATA_DIR` refer to the same persistent files used by Compose; honor any bind-directory overrides.
4. Run `minioj init-db`, and run it again to check idempotence. Verify preserved account/submission counts and that legacy admins are now system; the `roles-v2` marker must remain. If validation fails, keep both services stopped and restore the backed-up code/database/data together.
5. Build the new Server image (`docker compose build server`), start it and Nginx (`docker compose up -d`), then start the updated host Worker. Do not start an old image against the upgraded database. Verify health, login as the original administrator, `/manage/users`, current and historical submission results, and a new practice/contest submission.

Full permissions and semantics: [permissions](docs/permissions.md), [rejudge](docs/rejudge.md), [contest](docs/contest.md). Avatar decoding adds the Pillow runtime dependency; Server and Worker should use the same release. This development run does not apply the upgrade to the formal installation.

## Management, rejudge, profiles and contests

The red **管理** link appears immediately after MiniOJ for admin/system. `/manage` uses a dark sidebar and independent dense console; Problems, Contests and Submissions are available to both, Users/System only to system (direct access by admin returns 403). `/admin` redirects to the console; old problem-management URLs remain compatible. System cannot disable/demote itself or remove the last active system; the System page is read-only and never displays secrets.

In **Submissions**, filter by ID/user/problem/contest/language/status/verdict. Open a finished submission, click **Rejudge** and confirm. It retains ID, source and original creation time, queues the same Worker and preserves every result under **Judge History**. Active submissions return 409, a full queue returns 429, deleted problems cannot be rejudged. While judging, old AC does not count; the latest terminal result, including IE, becomes current. Profiles and standings recalculate from current results.

`/users/{username}` publicly shows join date, distinct solved problems, first current effective AC time, AC rate and recent submission metadata—not email or other users' source. **Settings → Avatar** accepts real PNG/JPEG/WebP up to 1 MiB and 4 megapixels, reencodes a maximum 256×256 PNG under a random local key, and falls back to the bundled default avatar. Username remains fixed; existing email/password/Token controls remain available.

In **Contests**, create a public contest with UTC start/end and existing problem IDs in A/B/C order. In the editor, enter an existing ID and click **Add problem**, use **Remove / ↑ / ↓**, then **Save contest**; without JavaScript, edit one ID per line. Admin/system can change the problem list even after start/end or when submissions exist. Removing an entry preserves its problem and submissions, but excludes it from standings; readding restores its in-window results. Only times lock after start, and running contests cannot be deleted.

Users can join explicitly or automatically on a contest submission. The contest problem page submits to `POST /api/v1/contests/{id}/submissions` with the ordinary SubmissionCreate body and 202 response; ordinary submissions remain practice. Only running contests accept submissions. ICPC standings count the first current AC and add 20 minutes for each preceding WA/RE/TLE/MLE/OLE; CE/IE do not penalize, and identical solved/penalty scores share rank. **Performance** is a backend difficulty-weighted logistic estimate for this contest only (0–4000, missing problem rating → 1200); it never affects ranking or permanent user rating. Refresh standings for current results, problem lists and ratings, or fetch the same public JSON at `GET /api/v1/contests/{id}/standings`. No database migration is needed for this increment. See [Contest rules and algorithm](docs/contest.md). Upcoming associations are hidden from normal users (their standings use an empty visible problem list and zero Performance), but existing practice problems are still public; this is not a private-problem contest system.

Regression commands (isolated databases/directories; Docker is needed for the smoke):

```bash
python -m pytest tests/test_management.py tests/test_management_migration.py tests/test_management_browser.py tests/test_contest_performance.py
python scripts/smoke_test_management.py
```

## First problem

1. Log in with the administrator account.
2. Open **管理 → Problems → New problem**.
3. Create the statement and limits. Problem IDs are 3–80 ASCII letters, numbers, or hyphens and may preserve mixed case. Statement fields support safe CommonMark plus KaTeX math: use `$...$` for inline formulas and a standalone `$$...$$` block for display formulas. Use **Preview Markdown** before saving. Embedded HTML is displayed as text and unsafe link schemes are not activated. KaTeX is bundled locally, so rendering does not require a browser CDN connection.
4. In **02 · Standard solution**, paste C++20 source and click **Save standard solution**, or upload a UTF-8 source file. The saved source is shown directly in the editor and can be edited there; it remains Admin-only. Save the standard solution separately from the problem statement.
5. Upload a testcase input as `sample` or `hidden`; the Worker runs the standard solution and stores stdout as the expected output. Alternatively, upload a C++20 generator to build one or more `generated` testcases.

Generator runs receive the seed in `argv[1]` and the 1-based case index in `argv[2]`; stdout becomes testcase input and is then passed to the standard solution. Jobs are asynchronous, so keep `minioj-worker` running and refresh the editor to see `FINISHED` or `FAILED`. A failed compile/run creates no testcase; a generator batch is stored atomically.

Each generated input or output is limited by `MINIOJ_TESTCASE_FILE_LIMIT_BYTES` (16 MiB by default). MiniOJ records SHA-256 checksums for both. Standard-solution and generator source files use `MINIOJ_SOURCE_LIMIT_BYTES`.

For an existing installation, follow **Upgrading an existing installation** above; stop both old processes before the one-time role migration. Once upgraded, ordinary restarts may use `./restart` and restart the separately running Worker.

Administrators can edit a problem, its standard solution, and its testcases even after submissions exist. Problem and submission pages do not show modification/deletion warnings. Existing results are retained without automatic rejudging. Internal revision checks remain: a queued submission whose problem changes before the Worker reads its data finishes with `IE` and an explanation; a running judge finishes using the data it already captured.

Other application warnings retain their **×** button for the current page. Problem-lifecycle warning dismissal and its browser-storage feature have been removed; no site-storage reset is needed. The editor's numbered sections and shortcuts separate the problem statement/limits, standard solution, testcase management, and build history. Input-file and generator builds have separate panels.

Deleting a problem removes it from listings and blocks new submissions. Opening its old URL shows a plain "Problem not found" page (HTTP 410); an unknown problem uses the same page with HTTP 404. Historical submissions remain viewable without lifecycle warnings. Deletion is logical: the database row, testcase files, and history remain stored, and the ID cannot be reused. Pending generator jobs are cancelled; replacing the standard solution rejects results from jobs using the previous solution. Multiple queued input/generator jobs using the same solution can still append testcases normally.

Keep Web and Worker on the same lifecycle rules. Startup adds compatible columns automatically, but this release's first upgrade requires the coordinated stop/backup/migrate/start procedure above. No database reset is needed.

A problem without testcases is accepted by the UI but its submissions receive `IE`, which makes incomplete judge data visible rather than silently accepting code.

### Per-test submission results

Open a submission's result page to see **Sample results** for samples and **Testcase results** for non-samples (hidden/generated), with the original test numbers, verdicts, recorded run time (`ms`) and peak memory (`KB`). All authorized viewers can see every point's status/resources. In `full` mode, click an available sample number to expand its input/answer and any stored actual output; only admin/system can expand non-sample data. Ordinary users never receive non-sample contents in the page. Empty groups are omitted. Previews remain byte-bounded, and changed problem revisions never attach current testcase files to historical results.

New runs use CPU time; historical timing values retain their original measurement method. Each table scrolls on mobile and long testsets; finished submissions appear automatically after polling. Judging still stops at the first failure, so remaining points show **Not run** with no resource measurements. Missing measurements show `—`; old submissions without per-test records show a resubmit notice instead of fabricated values. `diagnostic` retains all point metadata but disables every preview; `verdict_only` hides both tables. Submission ownership rules and shared HTTP fields are unchanged.

## Source preview and editor keys

Submission source previews use lightweight, local C++20 highlighting for keywords, comments, strings, numbers and preprocessor lines. **Copy code** copies the complete plain source, not highlighted HTML. It also attempts a local copy fallback on plain HTTP or when Clipboard API permission is denied; if both methods fail, select the source and copy it manually. No CDN or new source-access permission is involved.

In both the problem-page editor (practice/contest) and the management standard-solution editor, **Tab** inserts four spaces and indents selected lines; **Shift+Tab** unindents. Press **Esc, then Tab** to leave the editor using normal keyboard navigation. Other input fields retain ordinary Tab navigation. Source edits, submission bodies and stored code are unchanged except for the indentation you enter.

Testcase data previews display at most the first **1024 UTF-8 bytes**. A trailing `...` means more data exists but is not displayed; exact-limit and shorter values have no added marker. The marker is Web-only: saved data, machine Feedback strings and existing privacy rules are unchanged.

Server builds now exclude runtime avatars, problem data, jobs, databases and `.env`; existing bind-mounted data remains untouched. On 2026-10-02, a separate temporary BuildKit builder completed a no-cache build from the official Python base image and downloaded all application dependencies anew; the resulting image passed isolated deployment checks. The shared Docker daemon's unavailable proxy was not changed. Build proxy arguments may be passed to a separately configured working builder with `--build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY`; no proxy or credentials are baked into the image.

## Agent API quick start

Create a token under **Settings → API tokens**, then save the displayed secret. It cannot be recovered later.

```bash
export OJ_TOKEN='oj_replace_me'

curl -H "Authorization: Bearer $OJ_TOKEN" \
  http://localhost:8000/api/v1/agent/problems/two-sum

curl -X POST http://localhost:8000/api/v1/runs \
  -H "Authorization: Bearer $OJ_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"language":"cpp20","source_code":"int main(){return 0;}","stdin":""}'

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
| `GET` | `/api/v1/me` | Current identity and active `feedback_mode` |
| `GET/POST` | `/api/v1/tokens` | List or create own tokens |
| `DELETE` | `/api/v1/tokens/{id}` | Revoke an own token and retain its metadata (`204`) |
| `GET` | `/api/v1/problems` | Public problem list |
| `GET` | `/api/v1/problems/{id}` | Public problem detail |
| `GET` | `/api/v1/agent/problems/{id}` | Sanitized agent input |
| `POST` | `/api/v1/runs` | Synchronous response backed by the Worker Custom Run queue |
| `POST` | `/api/v1/submissions` | Queue a submission (`202`) |
| `GET` | `/api/v1/submissions/{id}` | Stable status/result fields |
| `GET` | `/api/v1/agent/submissions/{id}/feedback` | Structured diagnostic feedback |

Cookie-authenticated API mutations require the `X-CSRF-Token` header used by the bundled browser UI. Bearer-token requests do not.

Custom Run accepts `source_code`; the legacy `code` name remains compatible when only one is sent (or both values match). A full submission or Custom Run queue returns HTTP 429 with `Retry-After`; judge infrastructure failures and Custom Run Worker-wait timeouts return a sanitized HTTP 503. Successful submissions remain HTTP 202. No public Custom Run polling route was added.

Ordinary machine endpoints have explicit Pydantic request/response models in OpenAPI. Submission lookup always returns every documented field: while pending, `verdict`, `tests`, `resources`, and unavailable timestamps are JSON `null` rather than omitted. `time_ms` is milliseconds; `memory_kb` keeps its compatibility name but records KiB and is `null` when unavailable.

`started_at` is null before a Worker claims the submission; it may already be present in COMPILING/RUNNING. `finished_at` is null until completion. Pending does not mean every timestamp is null.

API failures use a stable envelope; `detail` remains temporarily for older clients. Validation details never echo the original password, token, source, or stdin value:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Unprocessable Entity",
    "details": [{"location": ["body", "language"], "message": "...", "type": "literal_error"}]
  },
  "detail": [{"location": ["body", "language"], "message": "...", "type": "literal_error"}]
}
```

`DELETE /api/v1/tokens/{id}` is an idempotent soft revoke: it sets `revoked_at`, immediately rejects the secret, and retains metadata/hash for audit. The Web UI keeps its existing form route but labels the action **Revoke**. There is no public hard-delete operation.

## Data and operations

### Frozen CodeHarness v1 and problem pages

The complete [HTTP v1 contract](docs/codeharness-api.md) documents authentication, discovery, pagination, status/verdict, units, Feedback, errors and idempotency. Existing paths and Phase 4 error-code names remain unchanged; no CodeHarness internals are added.

Web `/problems` shows **50 problems per page** with Previous/Next, literal ID/title search and **Default / Difficulty ↑ / Difficulty ↓**. Applying search/order returns to page 1; page links preserve parameters. Null ratings are last in either difficulty order and ties use ID ascending. Default Web ID ordering and API newest-first ordering remain. Root/subpath, mobile and no-JS operation are supported. API `GET /api/v1/problems?page=1&sort=difficulty_asc` retains its array body with `X-Total-Count`, `X-Page`, `X-Page-Size`, `X-Total-Pages`; omit page only for legacy unpaginated use. Explicit pages cannot exceed 50. Invalid page/order is 422, out-of-range is empty, and deleted problems are filtered before count/sort/page.

The existing `/api/v1/agent/submissions/{id}/feedback` has a typed `FeedbackResponse`; every authorized pending/final response is **200**, with required `submission_id/status/verdict/feedback_mode/failed_test/compile/execution/diagnostic/summary/summary_truncated`. Unavailable values are null; all eight verdicts share the model. Existing optional failure/tests/resources views remain. Ordinary submission resources retain their nested `resources.time_ms/memory_kb` names: runtime is CPU ms, compile time is wall ms, memory is KiB/unknown null.

| Feedback exposure | full | diagnostic | verdict_only |
| --- | --- | --- | --- |
| Safe summary, status/verdict/mode, failed index | yes | yes | yes |
| Safe compile diagnostics/resource/point metadata | yes | yes | no |
| Sample failure previews | yes | no | no |
| Hidden/generated previews | admin/system only | no | no |
| Internal errors, paths, traceback, secrets | never | never | never |

Mode is intersected with existing role/ownership permissions: **full is not unrestricted hidden access**, and query parameters cannot raise server policy. Text uses the common 1024 UTF-8 byte bound with explicit truncation flags. Compiler diagnostics are sanitized; summary/diagnostic messages are safe fixed descriptions. Stored results are untouched. Ordinary lookup and Web/history use the same boundary; verdict_only lookup has null tests/resources.

Optional `Idempotency-Key` on ordinary submission POST is an opaque 1–128-character ASCII key without spaces. Same user/key/effective payload replays the original 202/QUEUED response with the same ID, even after judging; a changed payload is 409 `idempotency_conflict`. Hashes, a database unique constraint and write locking protect concurrent retries. No key keeps the old independent-create behavior. The separate contest POST is unchanged. After an ambiguous POST timeout, **reuse the same key/body**; polling timeout must not create another submission.

The independent reference uses only standard-library HTTP/Bearer/JSON: no MiniOJ module, database or testcase file. Default code solves a two-integer sum; set `OJ_SOURCE_CODE` for a different problem. This invocation creates a submission on the installation you select:

```bash
export OJ_BASE_URL='http://localhost:8000'  # or http://localhost/minioj/
export OJ_API_TOKEN='oj_replace_me'
export OJ_PROBLEM_ID='sum-two'
export OJ_IDEMPOTENCY_KEY='save-a-unique-request-key'
python scripts/smoke_test_codeharness_api.py --timeout 120 --expect-verdict AC
```

Flow: **Me → paginated discovery → sanitized Problem → Submission → Poll → Feedback**. Polling waits 1/2/3/5/5… seconds; defaults are 60s total polling and 15s per request (`--timeout`/`--request-timeout`). Timeout reports `client polling timeout`. At most three automatic POST transport attempts reuse the key/payload. Save the printed key to resume a later invocation, rather than using a new key. No summary/HTML parsing is involved.

### Offline backup and isolated restore

Use a maintenance window: stop **all** Web, Worker and import/CLI writers (even authenticated GETs can update token metadata). Preserve the complete SQLite directory, including WAL/SHM/journal, **all data** (tests, statement assets, avatars), private configuration and matching release/judge-image version. Never copy DB and files at different live revisions. Ephemeral jobs are not required; interrupted work uses existing safe startup recovery.

After stopping your chosen deployment (`systemctl --user stop minioj-worker` and `docker compose stop nginx server` when applicable), select private absolute backup paths outside the repository and actual persistent source directories:

```bash
python scripts/backup_restore.py backup --writers-stopped \
  --database-dir ./database --data-dir ./data \
  --destination /absolute/private-backups/snapshot-20261002
```

`--database-name` defaults to `oj.db`. The tool creates a new 0700 snapshot, checks SQLite integrity/foreign keys and file hashes, detects source changes, and rejects symlinks/overlap. It does not stop/restart services. Securely save `.env` and release/image information **outside** the snapshot folder, with private permissions; they may contain secrets. Resume the unchanged installation only after success.

Restore into a **new** directory, never over the live installation:

```bash
python scripts/backup_restore.py restore \
  --snapshot /absolute/private-backups/snapshot-20261002 \
  --destination /absolute/isolated-restore
```

The tool checks the manifest/restored files/SQLite and refuses even an existing empty target. Point isolated Server/Worker at restored DB/data, a separate job directory, unique `MINIOJ_WORKER_OWNER`, loopback ports and matching code/image; verify a new AC before considering coordinated formal restoration. Failure leaves a newly created incomplete directory for inspection; old data is never overwritten/deleted. Restore code/config/DB/data as one release. CF source/cache and Polygon input archives can be saved separately; this tool does not remove them.

```bash
python scripts/smoke_test_phase5.py --all-verdicts
```

This smoke self-creates temporary data, performs backup → restore → real HTTP reference/root/subpath → Worker/Docker AC and all eight verdicts, verifies cleanup and restores the original snapshot again. The deployment smoke additionally invokes the reference through actual isolated Nginx. No formal database or production image tag is used.

### Polygon import

Sign in as admin/system, open **管理 → Problems → Import Polygon**, choose a full Polygon `.zip`, optionally enter a new ID, select a language, and click **Import problem**. Automatic selection prefers Chinese, then English. Packages need `problem.xml`, an HTML statement and all generated inputs/answers; an enclosing directory is supported. Generators, validators and package executables are not run during import.

Imports preserve the title, limits, source URL, test order, samples, generated/manual provenance, referenced images and main C++ source. C++ checker source and local headers are snapshotted with a SHA-256 digest in the database, not exposed through public/Agent responses. The package's declared checker source takes precedence, including custom multi-answer checkers. If a standard checker has no source, MiniOJ uses its pinned [official testlib](https://github.com/MikeMirzayanov/testlib) implementation: 19 non-scoring checkers including `ncmp`, `icmp`, `uncmp`, `lcmp`, `fcmp`, `rcmp4/6/9`, `wcmp`, and `nyesno`. Missing custom source, non-C++ checkers, scoring/interactive/named-file-I/O problems and TeX/PDF-only statements are rejected. Existing or deleted IDs are never overwritten. Database rows, checker source, tests and images are committed together; failed imports remove their new files.

Checker source must compile as C++20. Include required local `.h/.hpp/.hh/.hxx/.inc` resources in the source directory (including subdirectories), or declare them under `files/resources`; sources/headers must be UTF-8. Package `testlib.h` takes precedence, otherwise the pinned header is used. Bundles are limited to 128 files / 4 MiB, each header to 1 MiB; the entry source uses `MINIOJ_SOURCE_LIMIT_BYTES`. Imported binaries are never used. Each submission compiles its checker once in an isolated Docker job, then invokes it with input/output/answer files in a separate bounded container per testcase. Checker CPU/memory are excluded from contestant results. Default testlib codes 0 → AC, 1/2/4/8 → WA; failure, crash, timeout, OOM, output limit and scoring codes → safe IE. Checker diagnostics stay in server logs. `MINIOJ_CHECKER_TIME_LIMIT_MS=10000` and `MINIOJ_CHECKER_MEMORY_MB=512` configure independent checker limits; compilation uses the existing compile budget.

Run Sample remains execution-only for testlib problems: it displays expected/output but does not claim AC/WA using text comparison. Use Submit for the checker verdict. Custom Run and shared HTTP schemas are unchanged. Existing `lines`/`tokens`/`yesno` problems keep their behavior. After installing updated code, back up the database, run `minioj init-db` for the idempotent checker metadata upgrade, and restart/rebuild Server and Worker when ready; no automatic re-import or rejudge is performed.

Real, isolated checker verification (no production writes):

```bash
python scripts/smoke_test_checkers.py
python scripts/smoke_test_checkers.py --polygon-package 'store/graph-1-27$linux.zip'
```

Limits default to 64 MiB compressed (`MINIOJ_POLYGON_ARCHIVE_LIMIT_BYTES`), 256 MiB expanded (`MINIOJ_POLYGON_EXPANDED_LIMIT_BYTES`), 1000 tests and 10000 entries. Individual inputs/answers use the existing testcase limit. Only referenced PNG/JPEG/GIF/WebP images become public, under `data/problems/<id>/assets/`; include these in backups. Nginx's import location accepts 65 MiB including multipart overhead; change it when increasing the upload limit. Runtime `store/` contents are Git-ignored except the explicitly allowlisted optional [CF import tool](store/cf_import/README.md) source/example/docs; all of `store/` is excluded from the Server Docker context. MiniOJ core and the HTTP reference client do not depend on this tool or its model credentials.

### Execution timing

New runtime `time_ms` values record sandbox process-tree CPU milliseconds using cgroup v2, excluding Docker startup and scheduling/CLI waits; small supervisor CPU costs are included. The CPU limit has an independent in-container wall watchdog of `max(1000 ms, 5 × CPU limit)`, plus a 3000 ms Docker transport allowance. Explicit supervisor timeout flags replace exit-code inference. CPU frequency, caches and resource contention can still affect computation cost. Compilation retains its host wall-clock budget; historical results are not recalculated.

Execution stack limits follow the problem's memory budget, rather than Docker's default 8 MiB, so deep recursive solutions can use their allowed memory. The container's total RAM limit remains enforced.

After upgrading, rebuild the judge image with `docker build -t minioj-cpp20:latest docker/cpp20` and restart the Worker when ready to apply the change. Workers reject images missing the CPU supervisor label. Rebuilding the Server alone does not update the judge image.

- SQLite: `database/oj.db`
- Testcases: `data/problems/<problem-id>/tests/`
- Ephemeral job files: `MINIOJ_JOB_DIR`, falling back to `MINIOJ_DATA_DIR/jobs`; each completed run removes its own directory
- Maximum size of each testcase input or output file: `MINIOJ_TESTCASE_FILE_LIMIT_BYTES` (default 16777216 bytes)
- Standard-solution/generator run limit: `MINIOJ_TESTCASE_BUILD_TIME_LIMIT_MS` (default 10000 ms) and `MINIOJ_TESTCASE_BUILD_MEMORY_MB` (default 512 MiB)
- Maximum cases in one generator job: `MINIOJ_GENERATOR_MAX_CASES` (default 50)
- Maximum source size: `MINIOJ_SOURCE_LIMIT_BYTES` (default 262144 bytes)
- Maximum Custom Run input size: `MINIOJ_STDIN_LIMIT_BYTES` (default 262144 bytes)
- Compilation limit: `MINIOJ_COMPILE_TIME_LIMIT_MS` (default 30000 ms) and `MINIOJ_COMPILE_MEMORY_MB` (default 512 MiB, or the higher problem memory limit)
- Combined stdout/stderr limit: `MINIOJ_OUTPUT_LIMIT_BYTES` (default 8388608 bytes)
- Default token lifetime: `MINIOJ_TOKEN_DEFAULT_DAYS` (default 90 days)
- Queued submission / Custom Run limits: `MINIOJ_MAX_QUEUED_SUBMISSIONS` (1000) and `MINIOJ_MAX_QUEUED_RUNS` (16)
- Custom Run Worker wait / overload retry hint: `MINIOJ_CUSTOM_RUN_WAIT_SECONDS` (45) and `MINIOJ_OVERLOAD_RETRY_AFTER_SECONDS` (2)
- Worker Docker cleanup label: `MINIOJ_WORKER_OWNER` (default `worker`; make it unique for concurrent isolated MiniOJ instances)

`MINIOJ_JOB_DIR` is independently configurable so ephemeral compiler and runtime files can live outside testcase storage. Omitting it preserves the existing `data/jobs` layout. For a host-run Worker, `/tmp/minioj/jobs` is a suitable non-persistent choice. The directory used for a Docker bind mount must be visible to the Docker daemon. In Compose, the Server queues Custom Runs through SQLite and never needs Docker access; the host Worker must share the mounted database and data directories.

V1 allows one Worker per job directory. Custom Run, submission, and testcase-build claims use conditional updates, and the exclusive lock prevents a second local Worker from racing the active one. On restart, interrupted Custom Runs and testcase builds become `FAILED`, while interrupted `COMPILING`/`RUNNING` submissions finish as safe `IE`; none are automatically re-executed. SQLite, one Server process for atomic in-process capacity admission, and single-host execution remain the scaling boundary.

Run the fast, Docker-free checks with:

```bash
conda activate minioj
ruff format --check .
ruff check .
python -m pytest -ra
```

The minimal `.github/workflows/ci.yml` runs these full checks on Python 3.12, installs Chromium for browser tests, checks application JS, and parses Compose with `.env.example`. It uses no production secrets and runs no Docker Judge. Real Docker integration remains in the separate smoke scripts below. [Hosted CI for `40ede95`](https://github.com/Sy-SU/MiniOJ/actions/runs/37008639747) completed successfully with 725 tests; the initial four browser-fixture failures and their minimal fix are recorded in [TODO.md](TODO.md). Earlier local/Docker acceptance remains a dated snapshot in [phase5-validation.md](docs/phase5-validation.md), not production deployment evidence.

With Docker and the judge image available, run the isolated end-to-end checks:

```bash
python scripts/smoke_test_stack.py
python scripts/smoke_test_judge.py
python scripts/smoke_test_generator.py
python scripts/smoke_test_sandbox.py
python scripts/smoke_test_worker_failures.py
python -m playwright install chromium
python scripts/smoke_test_phase3_deploy.py
python scripts/smoke_test_timing.py
python scripts/smoke_test_phase5.py --all-verdicts
```

To test browser file selection, Nginx upload, imported images, Run Sample and a full main-solution submission with a local package:

```bash
python scripts/smoke_test_phase3_deploy.py --polygon-package 'store/graph-1-27$linux.zip'
```

`--server-image IMAGE` skips the fresh Server build and verifies that image's runtime behavior. `MINIOJ_DOCKER_IMAGE` selects the smoke Worker image. Timing smoke accepts `--baseline-image IMAGE` to compare the previous host-wall measurement and covers background load, descendant CPU limits, UID/capability isolation and report tampering.

The scripts cover the temporary-database stack flow; all verdicts plus exit-code and output-limit boundaries; a real generator/std pair; network, PID, host-file, read-only-root and cleanup isolation; safe IE handling for corrupt/missing testcase files and an unavailable image; and a real Chromium flow through isolated Compose/Nginx plus a host Worker at `/minioj/`. The deployment smoke uses temporary database/data directories, random loopback ports, and its own Compose project. It verifies browser login/token creation, Run Sample, Custom Test, Submit/automatic result refresh, AC/CE/TLE, Docker-fault 503, and cleanup. It also invokes both the legacy `smoke_test_phase4_api.py` and new `smoke_test_codeharness_api.py`, neither importing MiniOJ, through the Nginx subpath and Server root path. It does not deploy production.

To exercise an already running installation with the standalone HTTP client (this creates and revokes a child token, runs code, and creates one submission):

```bash
export OJ_BASE_URL='http://localhost:8000'
export OJ_API_TOKEN='oj_replace_me'
export OJ_PROBLEM_ID='two-sum'
python scripts/smoke_test_phase4_api.py
```

The `pytest` suite does not require Docker and does not execute untrusted binaries. Browser tests in `tests/test_problem_pages.py` use Playwright Chromium and skip if Playwright or Chromium is not installed; after `python -m playwright install chromium`, run `pytest tests/test_problem_pages.py` to verify warning-free history pages, problem links, and unrelated warning buttons. Docker smoke scripts require a running Docker daemon and the `minioj-cpp20:latest` image; the Phase 3 deployment smoke also requires the Playwright Chromium download.

## Rebuild and restart

Run `./restart` from the project root after changing the source or Dockerfile. It detects the WSL networking mode: NAT uses the current IPv4 default gateway (the Windows host), while mirrored mode uses `127.0.0.1`; the default proxy port is `7897`. Older WSL installations without networking-mode information fall back to the NAT gateway. Override detection with `MINIOJ_RESTART_PROXY=http://HOST:PORT ./restart`. Keep the Windows proxy running and accessible from WSL.

The script exports uppercase and lowercase HTTP/HTTPS proxy variables, then checks the dedicated `minioj-builder` BuildKit container. When the proxy changes, it recreates this builder with `docker buildx rm --keep-state` and reuses its cache volume. It explicitly selects that builder for `docker compose build` and passes proxy build arguments to dependency installation. After a successful build it starts Compose with `--no-build --wait`, then restarts Nginx. Failed preparation/build steps stop before services are restarted. Do not run it concurrently with another build using `minioj-builder`.

The script does not modify `.env`, the calling shell, or Docker daemon settings. A cached BuildKit image can bootstrap the builder even when the daemon's old proxy is unavailable. On a fresh machine, pulling the BuildKit image (or using `docker pull` directly) still requires the Docker daemon to have a working proxy. See [Docker's cache persistence documentation](https://docs.docker.com/build/builders/drivers/docker-container/#cache-persistence).

## Account input limits

- New usernames contain 3–10 ASCII letters (`A–Z`, `a–z`), with surrounding whitespace removed. Administrator names remain reserved for public registration. Existing accounts can still log in with their original usernames.
- Email addresses are trimmed and lowercased, with at most 254 characters total and 64 before `@`. ASCII addresses with a dotted domain and `+` tags are supported; whitespace, control characters, display names, and quoted addresses are rejected.
- Passwords require at least 10 characters and at most 1024 UTF-8 bytes. Spaces, symbols, and Unicode are allowed without trimming or truncation. Registration and password changes require matching confirmation; login and current-password verification also reject oversized passwords.
- Account POST bodies are limited to 16 KiB (HTTP 413), including chunked requests without `Content-Length`.
- Source, stdin, and API testcase JSON bodies are bounded before JSON parsing using their configured field limits plus JSON-encoding overhead; decoded fields are still checked by UTF-8 byte length. Web standard-solution/generator/testcase multipart and form requests have streaming total-body and per-file limits.
- Browser limits accompany server validation, parameterized database queries, and automatic HTML escaping.
- Newly generated API tokens have a **Copy** button in Settings, with manual selection available if clipboard access fails. The full token is shown only once. Lists show its first 7 and last 4 characters separated by 8 asterisks, e.g. `oj_abcd********wxyz`. Only the hash and masked preview are stored. Older tokens show an unavailable-preview label because their hashes cannot recover these characters. **Revoke** immediately invalidates a token while retaining its metadata; revoked tokens cannot be used and no longer show an action button.

## Security notes

- Set a strong, persistent `MINIOJ_SECRET_KEY`; changing it invalidates browser sessions.
- Put the server behind HTTPS and set `MINIOJ_SESSION_HTTPS_ONLY=true` outside local development.
- Do not expose Docker's unauthenticated TCP socket.
- Use a dedicated host/VM for adversarial workloads. Docker isolation reduces risk but is not a substitute for a hardened kernel sandbox for hostile public judging.
- Back up the SQLite database and `data/problems` together so testcase metadata and files remain consistent.
