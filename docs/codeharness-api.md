# MiniOJ HTTP v1 contract

Frozen for the Phase 5 implementation on 2026-10-02. MiniOJ core remains independently usable and does not implement CodeHarness internals (Agent Loop, providers, prompts or a shared client workspace). The optional repository-local CF import utility is separate tooling, not a Core or reference-client dependency. OpenAPI at `/openapi.json` is backed by explicit Pydantic response models and regression tests. Local acceptance and the 723-test release audit are complete (READY_WITH_NOTES); evidence and image metadata differences are recorded in [TODO.md](../TODO.md). Hosted CI and production upgrade/deployment remain unexecuted; this audit does not commit/push.

## Authentication and discovery

Use `OJ_BASE_URL` for the installation URL (e.g. `http://localhost:8000` or `http://localhost/minioj/`) and `OJ_API_TOKEN` for an existing Web-created token. Send `Authorization: Bearer <token>`; never put the token in a URL. The client does not follow redirects with credentials. Existing Session/CSRF and role rules are unchanged; `/agent/` endpoints require Bearer.

1. `GET /api/v1/me`: includes `feedback_mode: full|diagnostic|verdict_only` from the Server's loaded `MINIOJ_FEEDBACK_POLICY`.
2. `GET /api/v1/problems?page=1&sort=default`: discover IDs in pages of **50**. The JSON body remains the existing array, not a new envelope. Optional headers `X-Total-Count`, `X-Page`, `X-Page-Size`, `X-Total-Pages` describe a paginated request and are declared in OpenAPI; runtime behavior is unchanged. Continue until an empty/short page, or the reported last page. Without `page`, the old unpaginated array remains compatible; new clients must opt into pagination.
3. `GET /api/v1/agent/problems/{problem_id}`: fetch the solver's statement, specifications, notes, limits and public samples. Do not supplement it with ordinary detail metadata excluded from the solver context. No rating, tags, editorial, std, checker sources or hidden/generated contents are returned.

`page` is 1-based; zero/negative/non-integer is safe 422; out-of-range is `[]`. Empty lists have one empty page. There is no configurable `page_size`: an explicit page always has at most 50 items. `sort=default|difficulty_asc|difficulty_desc`; null rating is last in both difficulty orders, equal ratings use problem ID ascending. Web default remains ID ascending; ordinary API default remains created_at descending, with ID as a stable tie-breaker. Sorting/pagination occur in SQL. Optional `q` (at most 200 characters) searches ID/title as a literal substring. Deleted problems are excluded from results and counts. Offsets are stable for an unchanged library, not a snapshot across concurrent additions/removals.

## Accept, poll, feedback

`POST /api/v1/submissions`, body:

```json
{"problem_id":"sum-two","language":"cpp20","source_code":"..."}
```

Success remains **202**, `{"submission_id":123,"status":"QUEUED"}`. No required request field or existing path is renamed. The ID field is `submission_id`, not a newly introduced `id` alias.

`GET /api/v1/submissions/{submission_id}` uses the unchanged `SubmissionDetailResponse`: all of `submission_id`, `problem_id`, `language`, `status`, `verdict`, `tests`, `resources`, `created_at`, `started_at`, `finished_at` always exist. Nonterminal verdict/tests/resources and unavailable timestamps are `null`. Resources retain their existing nested shape `resources.time_ms/memory_kb`; there are no duplicate top-level resource aliases.

`created_at` is present from acceptance. `started_at` is null before claim, but may be populated in COMPILING/RUNNING; `finished_at` remains null until completion. The null rule concerns unavailable timestamps, not all timestamps in pending states.

Status is lifecycle, **QUEUED → COMPILING → RUNNING → FINISHED** (CE/IE may finish early). Verdict is final **AC/WA/CE/RE/TLE/MLE/OLE/IE**, otherwise `null`. Clients must never infer these from summary, compiler exit codes, HTML or natural-language errors. Timestamps use UTC; existing timezone-less SQLite timestamps also represent UTC. Runtime `time_ms` is process-tree CPU milliseconds; compilation time is wall milliseconds. `memory_kb` is the compatibility name for **KiB**, unknown is `null`. Stored historical measurements retain their original method. Submission resources are the maximum of executed tests; CE reports compile resources.

The existing **`GET /api/v1/agent/submissions/{submission_id}/feedback`** is the only Feedback endpoint; no duplicate `/submissions/{id}/feedback` route was added. If authorized and found, it returns **200** in every lifecycle/verdict state. `FeedbackResponse` always includes:

| Field | Meaning |
| --- | --- |
| submission_id, status, verdict | Same identity, current lifecycle and final verdict as submission lookup |
| feedback_mode | Server policy; cannot be raised by a query parameter |
| failed_test | Positive 1-based index when known; otherwise null, including pending |
| compile | Safe CE/failed-compile metadata and bounded diagnostics, otherwise null |
| execution | The existing resource summary projected without additional storage; null when unavailable/restricted; CE uses compile resource measurements |
| diagnostic | Safe fixed message with message_truncated; null in verdict_only |
| summary, summary_truncated | Safe explanatory compatibility text, never machine control flow |

Pending compile/execution/failed_test/verdict are null. Existing optional views `failure`, `tests`, `resources`, `limits`, `test_results`, `testcase_types`, `stdout_truncated`, `stderr_truncated` retain names when available and allowed. They are typed allowlisted projections, never serialized internal Judge dictionaries. In particular, restricted legacy `failure` remains `{"test_index":7}`, not a dictionary full of null content fields. Older rows without metadata do not acquire fabricated measurements.

All eight final verdicts have this same schema. AC has no failure for normal data; WA/RE/TLE/MLE/OLE carry a known failure index; CE provides compile diagnostics if allowed and normally no failed_test; IE uses a fixed safe message, with no raw internal error or compile text. Tests also cover all three pending states.

## Feedback mode × role

Final exposure is **mode allowlist ∩ role permission**. Ownership and management capabilities are checked before applying a mode. Full does **not** grant ordinary users access to hidden/generated data.

| Exposed field | full | diagnostic | verdict_only |
| --- | --- | --- | --- |
| Identity/status/verdict/mode and safe summary | yes | yes | yes |
| failed_test (when known) | yes | yes | yes |
| Compile error metadata and sanitized stdout/stderr | yes | yes | no |
| Safe diagnostic message | yes | yes | no |
| Resource/test/point metadata | yes | yes | no |
| Sample failure input/expected/actual/stderr | yes | no | no |
| Hidden/generated failure previews | admin/system only | no, even admin/system | no, even admin/system |
| Docker diagnostics, traceback, internal paths/SQL/secrets | never | never | never |

Each exposed compile stdout/stderr, runtime stderr and failure input/expected/actual preview is at most **1024 UTF-8 bytes**, without a broken Unicode character. Each has an explicit `<field>_truncated` flag; already-truncated upstream flags survive. Runtime stdout is exposed only as the authorized failure's `actual`, never hidden output via a second field. Diagnostics and summary are fixed bounded messages with truncation flags. Compiler diagnostics redact paths, token/secret forms and infrastructure lines; successful compiler output is not exposed. Internal saved results are not shortened or rewritten. Web and Judge History share this allowlist; existing Web-only authorized testcase file previews remain revision-guarded. A Web-only trailing `...` marks truncation; JSON strings do not gain ellipses.

Ordinary Submission lookup also applies the policy: verdict_only returns null tests/resources; it never contains source or raw results. Source access remains the existing owner/admin/system Web permission. IE never publishes raw failure contents through Feedback. Admin/system Web file previews still require full and existing content permission.

## Idempotency and timeouts

Optional **`Idempotency-Key`**, 1–128 printable ASCII characters without spaces, is supported on the ordinary submission POST. Generate/save an opaque key before the first POST; on a transport failure retry the same effective payload with the same key.

- Same user/key/payload → same submission ID and original **202/QUEUED** acceptance response, even after judging, rejudge, queue fill or problem deletion. Poll the ID for current state.
- Same user/key/different valid payload → **409**, `error.code=idempotency_conflict`; no second submission.
- Keys are user-scoped, not token-scoped. Canonical accepted problem/language/exact source/contest association form the payload hash; ignored JSON extras do not change the effective request. Hashes, not raw keys, are stored. No expiry or reuse is introduced; replays remain valid while the submission is retained.
- A database unique index `(user_id,idempotency_key_hash)` plus a SQLite write lock before lookup/insert makes retries safe across connections/processes; it does not rely on the process-local queue mutex. Legacy rows have null request hashes, and requests without a key retain independent-create behavior. New keys still obey CSRF, validation, queue capacity and ownership.
- Contest POST has not gained this header; its existing contract is unchanged. This is not support for distributed/multi-Worker scheduling.

Reference polling uses 1, 2, 3, 5, 5… seconds, not a tight loop. `--timeout` defaults to 60 seconds for total polling; `--request-timeout` defaults to 15 seconds per HTTP operation. Poll timeout reports **`client polling timeout`** and never creates another submission. POST transport retries are bounded to three attempts with the same key/body. Save the printed key (or set `OJ_IDEMPOTENCY_KEY`) to resume a later invocation; do not rerun with a new key after an ambiguous POST timeout.

## Errors

Phase 4's envelope remains `{"error":{"code":"...","message":"...","details":null},"detail":"..."}`; `detail` is compatibility only. Existing code names are deliberately preserved:

| Condition | HTTP | code |
| --- | --- | --- |
| Missing/invalid/expired/revoked identity | 401 | authentication_required |
| Insufficient role/CSRF | 403 | forbidden |
| Missing/deleted problem or another user's submission | 404 | not_found |
| Invalid request | 400 / 422 / 413 | bad_request / validation_error / payload_too_large |
| Full queue, with Retry-After | 429 | rate_limited |
| Other conflict / key-payload conflict | 409 | conflict / idempotency_conflict |
| Unavailable judge/Worker timeout (Custom Run) | 503 | service_unavailable |
| Unexpected server error | 500 | internal_error |

There is no second error format and no renaming to unauthorized/queue_full/infrastructure_unavailable. Validation details omit input values. Internal errors expose neither exception text, SQL, paths, source nor credentials. Parse HTTP/error.code, not error.message/summary. Final judge infrastructure failure is FINISHED/IE with HTTP 200 feedback, not a submission lookup 503.

## Independent reference and verification

`scripts/smoke_test_codeharness_api.py` imports only Python's standard library. It uses only HTTP/Bearer/JSON, never MiniOJ modules, SQLite, data/testcase files or shared models. The flow is **Me → paginated discovery → sanitized Problem → Submission → Poll → Feedback**. Default source solves the demo two-integer sum only; set `OJ_SOURCE_CODE` for a different problem.

```bash
export OJ_BASE_URL='http://localhost:8000'  # or http://localhost/minioj/
export OJ_API_TOKEN='oj_replace_me'
export OJ_PROBLEM_ID='sum-two'
export OJ_IDEMPOTENCY_KEY='save-a-unique-request-key'
python scripts/smoke_test_codeharness_api.py --timeout 120 --expect-verdict AC
```

This invocation creates a submission on the chosen installation. The isolated `python scripts/smoke_test_phase5.py --all-verdicts` instead creates, backs up and restores its own temporary DB/data, then uses real HTTP, the separate client and Docker Judge; it never uses the formal database. `smoke_test_phase3_deploy.py` additionally runs the reference against real isolated Nginx `/minioj` and direct Server root paths.

Regression: `test_phase5.py` locks OpenAPI, modes/verdicts/roles/pending states and final-body hidden sentinels; `test_submission_idempotency.py` covers retries, conflicts, uniqueness, concurrency and old-schema upgrades; `test_problem_pagination.py` covers boundary counts, SQL, root/subpath/mobile/no-JS; `test_codeharness_client.py` covers HTTP-only use, dropped responses and polling timeout. Offline operations and restore instructions are in both READMEs.
