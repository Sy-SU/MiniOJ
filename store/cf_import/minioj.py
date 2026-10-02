from __future__ import annotations

import os
import time

from .config import MiniOJOptions
from .http import HTTPClient, HTTPError


class MiniOJClient:
    def __init__(self, cfg: MiniOJOptions, http: HTTPClient | None = None):
        self.cfg = cfg
        self.http = http or HTTPClient()
        self.base = cfg.base_url.rstrip("/") + "/api/v1"

    def request(self, method: str, path: str, payload=None):
        token = os.getenv(self.cfg.token_env, "")
        if not token:
            raise ValueError(f"Set {self.cfg.token_env} to a MiniOJ admin Bearer token")
        return self.http.request(method, self.base + path, payload=payload, token=token)

    def ensure_admin(self):
        me = self.request("GET", "/me")
        if me.get("role") not in {"admin", "system"}:
            raise ValueError("Importer requires an active MiniOJ admin token")

    def find(self, external_id: str, stable_id: str) -> dict | None:
        rows = self.request("GET", "/problems")
        matches = [
            p
            for p in rows
            if (p.get("source") or "").casefold() == "codeforces"
            and p.get("source_id") == external_id
        ]
        collisions = [
            p for p in rows if p["problem_id"] == stable_id and p not in matches
        ]
        if collisions or len(matches) > 1:
            raise ValueError(
                "MiniOJ source/id collision; refusing to create or overwrite"
            )
        return matches[0] if matches else None

    def testcases(self, problem_id: str) -> list[dict]:
        return self.request("GET", f"/admin/problems/{problem_id}/testcases")

    def ensure_problem(self, payload: dict, existing: dict | None) -> str:
        if existing:
            problem_id = existing["problem_id"]
            self.request(
                "PUT", "/admin/problems/" + problem_id, dict(payload, id=problem_id)
            )
        else:
            problem_id = payload["id"]
            try:
                self.request("POST", "/admin/problems", payload)
            except HTTPError as exc:
                if exc.status != 409:
                    raise
                # A previous create response may have been lost before state commit.
                found = self.find(payload["source_id"], problem_id)
                if found is None:
                    raise ValueError(
                        "Problem id is reserved/deleted or has a different source"
                    ) from exc
                problem_id = found["problem_id"]
                self.request(
                    "PUT", "/admin/problems/" + problem_id, dict(payload, id=problem_id)
                )
        return problem_id

    def verify(self, submission_id: int) -> dict:
        deadline = time.monotonic() + self.cfg.verification_timeout_seconds
        while True:
            result = self.request("GET", f"/submissions/{submission_id}")
            if result.get("status") == "FINISHED":
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "MiniOJ verification deadline exceeded; resume polls the saved submission id"
                )
            time.sleep(self.cfg.poll_seconds)
