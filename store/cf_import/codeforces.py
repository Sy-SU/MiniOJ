from __future__ import annotations

import os

from .config import Config
from .http import HTTPClient, RequestPacer
from .scanner import ProblemId
from .storage import digest, now, read_json, write_json


class CodeforcesClient:
    def __init__(self, cfg: Config, http: HTTPClient | None = None):
        self.cfg = cfg
        self.http = http or HTTPClient()
        self.pacer = RequestPacer(cfg.cache_dir / "cf-last-request.json")

    def _get(self, endpoint: str, params: dict, *, token: str = ""):
        self.pacer.wait(self.cfg.codeforces.interval_seconds)
        data = self.http.request("GET", endpoint, params=params, token=token)
        if not isinstance(data, dict) or data.get("status") != "OK":
            raise RuntimeError("Codeforces API returned FAILED or an invalid envelope")
        return data["result"]

    def sync_ac(self, *, refresh: bool = False) -> tuple[set[ProblemId], dict]:
        cf = self.cfg.codeforces
        if not cf.handle:
            raise ValueError(
                "Set codeforces.handle or CF_HANDLE before syncing AC status"
            )
        endpoint = cf.status_url or cf.api_url.rstrip("/") + "/user.status"
        identity = digest(cf.handle.casefold() + "\n" + endpoint)[:24]
        cache = self.cfg.cache_dir / "accounts" / identity
        manifest = cache / "accepted.json"
        if manifest.exists() and not refresh:
            data = read_json(manifest)
            return {
                ProblemId(p["contest_id"], p["index"]) for p in data["accepted"]
            }, data
        accepted: dict[ProblemId, list[int]] = {}
        seen_ids: set[int] = set()
        for page in range(cf.max_pages):
            params = {
                "handle": cf.handle,
                "from": page * cf.page_size + 1,
                "count": cf.page_size,
            }
            submissions = self._get(
                endpoint, params, token=os.getenv(cf.status_token_env, "")
            )
            if not isinstance(submissions, list):
                raise TypeError("user.status result must be a list of submissions")
            write_json(cache / f"page-{page + 1:04d}.json", submissions)
            for submission in submissions:
                sid = submission["id"]
                if not isinstance(sid, int) or sid in seen_ids:
                    raise ValueError(
                        "Repeated/invalid submission id; pagination is unreliable, rerun sync"
                    )
                seen_ids.add(sid)
                handles = [
                    m.get("handle", "").casefold()
                    for m in submission.get("author", {}).get("members", [])
                ]
                if cf.handle.casefold() not in handles:
                    raise ValueError(
                        "user.status returned a submission by a different/missing author"
                    )
                problem = submission.get("problem", {})
                if submission.get("verdict") == "OK" and isinstance(
                    problem.get("contestId"), int
                ):
                    pid = ProblemId(problem["contestId"], problem["index"].upper())
                    accepted.setdefault(pid, []).append(sid)
            if len(submissions) < cf.page_size:
                break
        else:
            raise ValueError(
                "Submission history exceeded max_pages; no incomplete AC set will be used"
            )
        data = {
            "handle": cf.handle,
            "fetched_at": now(),
            "submission_count": len(seen_ids),
            "accepted": [
                {"contest_id": p.contest_id, "index": p.index, "submission_ids": ids}
                for p, ids in sorted(accepted.items())
            ],
        }
        write_json(manifest, data)  # Only commit after the complete history succeeds.
        return set(accepted), data

    def metadata(self, wanted: list[ProblemId]) -> dict[ProblemId, dict]:
        if not wanted:
            return {}
        cache = self.cfg.cache_dir / "problemset.json"
        if not cache.exists():
            data = self._get(
                self.cfg.codeforces.api_url.rstrip("/") + "/problemset.problems",
                {"lang": "en"},
            )
            if not isinstance(data, dict) or not isinstance(data.get("problems"), list):
                raise ValueError("Invalid problemset.problems result")
            write_json(cache, data)
        data = read_json(cache)
        problems = {
            ProblemId(p["contestId"], p["index"].upper()): p
            for p in data["problems"]
            if isinstance(p.get("contestId"), int)
        }
        result = {}
        for pid in wanted:
            if pid not in problems:
                continue
            value = dict(
                problems[pid],
                problem_id=pid.key,
                source="Codeforces",
                external_id=pid.external_id,
                source_url=pid.url,
            )
            write_json(
                self.cfg.generated_dir
                / str(pid.contest_id)
                / pid.index
                / "metadata.json",
                value,
            )
            result[pid] = value
        return result
