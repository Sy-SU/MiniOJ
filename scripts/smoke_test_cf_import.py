"""Real Docker + HTTP + Worker smoke; synthetic CF/LLM, entirely temporary data."""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="minioj-cf-import-smoke-") as temp:
        root = Path(temp)
        os.environ.update(
            MINIOJ_DATABASE_URL=f"sqlite:///{root / 'oj.db'}",
            MINIOJ_DATA_DIR=str(root / "data"),
            MINIOJ_JOB_DIR=str(root / "jobs"),
            MINIOJ_SECRET_KEY="isolated-cf-import-smoke-secret-key",
            MINIOJ_WORKER_OWNER="cf-smoke-" + root.name,
            MINIOJ_ROOT_PATH="",
            CF_IMPORT_LLM_API_KEY="fixture-key",
        )
        import uvicorn

        from minioj.database import SessionLocal, init_db
        from minioj.models import ApiToken, User
        from minioj.security import create_api_token, hash_password
        from minioj.server.main import app
        from minioj.worker import main as worker_main
        from scripts.cf_import_fixture import (
            FIXTURES,
            PID,
            SPECIAL_PID,
            UpstreamHTTP,
            constructive_reference,
        )
        from store.cf_import.codeforces import CodeforcesClient
        from store.cf_import.config import Config
        from store.cf_import.generation import Sandbox
        from store.cf_import.llm import LLMClient
        from store.cf_import.minioj import MiniOJClient
        from store.cf_import.pipeline import Importer
        from store.cf_import.statement import StatementProvider

        init_db()
        token_id, token, hashed, expires = create_api_token()
        with SessionLocal() as db:
            admin = User(
                username="cfsmoke",
                email="cfsmoke@example.test",
                password_hash=hash_password("isolated-fixture-password"),
                role="system",
            )
            db.add(admin)
            db.flush()
            db.add(
                ApiToken(
                    id=token_id,
                    user_id=admin.id,
                    token_hash=hashed,
                    expires_at=expires,
                    name="isolated smoke",
                )
            )
            db.commit()
        os.environ["CF_IMPORT_MINIOJ_TOKEN"] = token
        cfg = Config(
            source_dir=root / "sources",
            generated_dir=root / "generated",
            cache_dir=root / "cache",
            state_file=root / "state.json",
        )
        cfg.codeforces.handle = "FixtureUser"
        cfg.llm.base_url = "https://fixture.invalid/v1"
        cfg.llm.model = "fixture"
        cfg.llm.repair_attempts = 0
        cfg.minioj.poll_seconds = 0.1
        cfg.minioj.verification_timeout_seconds = 120
        source = cfg.source_dir / str(PID.contest_id) / "A.cpp"
        source.parent.mkdir(parents=True)
        source.write_text((FIXTURES / "reference.cpp").read_text())
        upstream = UpstreamHTTP()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cfg.minioj.base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", access_log=False)
        )
        web = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        worker_main.running = True
        worker = threading.Thread(
            target=worker_main.run_worker, args=(0.05,), daemon=True
        )
        web.start()
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Isolated HTTP server did not start")
                time.sleep(0.05)

            def importer():
                return Importer(
                    cfg,
                    cf=CodeforcesClient(cfg, upstream),
                    statements=StatementProvider(cfg, upstream),
                    llm=LLMClient(cfg.llm, upstream),
                )

            dry = importer().run(dry_run=True)
            if dry["dry_run_success"] != 1:
                raise RuntimeError(f"Dry-run failed: {dry}")
            remote = MiniOJClient(cfg.minioj)
            if remote.request("GET", "/problems"):
                raise RuntimeError("Dry-run created a remote problem")
            result = importer().run()
            if result["imported"] != 1:
                raise RuntimeError(f"Import failed: {result}")
            rows = remote.testcases(PID.minioj_id)
            if len(rows) != 13:
                raise RuntimeError("Wrong remote test count")
            if (
                importer().run()["already_imported"] != 1
                or len(remote.testcases(PID.minioj_id)) != 13
            ):
                raise RuntimeError("Repeat import was not idempotent")

            special_cfg = cfg.model_copy(deep=True)
            special_cfg.source_dir = root / "special-sources"
            special_cfg.generated_dir = root / "special-generated"
            special_cfg.cache_dir = root / "special-cache"
            special_cfg.state_file = root / "special-state.json"
            source = special_cfg.source_dir / str(SPECIAL_PID.contest_id) / "B.cpp"
            source.parent.mkdir(parents=True)
            source.write_text(constructive_reference())
            special_upstream = UpstreamHTTP(constructive=True)

            def special_importer():
                return Importer(
                    special_cfg,
                    cf=CodeforcesClient(special_cfg, special_upstream),
                    statements=StatementProvider(special_cfg, special_upstream),
                    llm=LLMClient(special_cfg.llm, special_upstream),
                )

            result = special_importer().run()
            if result["imported"] != 1:
                raise RuntimeError(f"Constructive import failed: {result}")
            meta = remote.request(
                "GET", f"/admin/problems/{SPECIAL_PID.minioj_id}/checker"
            )
            assert meta["checker"] == "testlib" and meta["sha256"]
            assert len(remote.testcases(SPECIAL_PID.minioj_id)) == 13
            assert special_importer().run()["already_imported"] == 1
            # A solution with a different construction also passes the actual worker.
            alternate = constructive_reference().replace(
                'std::cout << 0 << " " << sum <<',
                'std::cout << sum + 1 << " " << -1 <<',
            )
            for program, verdict in (
                (alternate, "AC"),
                ('#include <iostream>\nint main(){std::cout << "0 0\\n";}', "WA"),
            ):
                created = remote.request(
                    "POST",
                    "/submissions",
                    {
                        "problem_id": SPECIAL_PID.minioj_id,
                        "source_code": program,
                    },
                )
                judged = remote.verify(created["submission_id"])
                assert judged["verdict"] == verdict, judged
            print(
                "Constructive importer: checker self-tests, alternative-answer Worker AC, incorrect-answer WA, repeat skip passed",
                flush=True,
            )
            # Ensure the importer catches degenerate LLM checkers before any upload.
            from store.cf_import.checkers import CheckerHarness
            from store.cf_import.contracts import ProblemSpec
            from store.cf_import.generation import CheckerError
            from store.cf_import.storage import read_json

            special_dir = special_cfg.generated_dir / str(SPECIAL_PID.contest_id) / "B"
            special_sandbox = Sandbox(special_cfg, special_dir)
            original_checker = (special_dir / "checker.cpp").read_text()
            spec = ProblemSpec.model_validate(
                read_json(special_dir / "problem_spec.json")
            )
            bad_checker_dir = root / "bad-checker"
            bad_checker_dir.mkdir()
            for exit_code, case_output, accept in (
                (0, "999 0", False),
                (1, "6 0", True),
                (3, "6 0", True),
            ):
                (special_dir / "checker.cpp").write_text(
                    f"int main(){{return {exit_code};}}"
                )
                harness = CheckerHarness(special_sandbox, bad_checker_dir, spec)
                try:
                    harness.check(
                        "1\n3\n1 2 3\n",
                        case_output,
                        "6 0",
                        "degenerate-checker",
                        accept=accept,
                    )
                except CheckerError:
                    pass
                else:
                    raise RuntimeError("Degenerate checker passed validation")
            (special_dir / "checker.cpp").write_text(original_checker)
            print(
                "Always-accept / always-reject / internal-error checkers blocked before upload",
                flush=True,
            )

            # Probe the same production sandbox path with hostile generated programs.
            directory = cfg.generated_dir / str(PID.contest_id) / PID.index
            sandbox = Sandbox(cfg, directory)
            probe = root / "probe"
            probe.mkdir()
            secret = root / "host-secret"
            secret.write_text("HOST-ONLY")
            probe_source = r"""#include <fstream>
#include <iostream>
#include <sys/socket.h>
#include <arpa/inet.h>
int main(int argc, char**) {
    if (argc > 1) { while (true) {} }
    std::ifstream secret("SECRET_PATH"), env("/work/.env"), docker("/var/run/docker.sock");
    if (secret || env || docker) return 10;
    std::ofstream write("/work/escape"); if (write) return 11;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in a{}; a.sin_family=AF_INET; a.sin_port=htons(53); inet_pton(AF_INET,"8.8.8.8",&a.sin_addr);
    if (connect(fd,(sockaddr*)&a,sizeof a)==0) return 12;
    std::cout << "isolated\n";
}""".replace("SECRET_PATH", str(secret))
            sandbox.compile(probe, probe_source, "isolation-compile")
            output = sandbox.run(probe, "", "isolation-run")
            if output != "isolated\n":
                raise RuntimeError("Sandbox isolation probe failed")
            timed = sandbox.judge.execute(
                probe, "", 200, 128, arguments=["loop"], output_limit=4096
            )
            if not timed.timed_out:
                raise RuntimeError("Sandbox failed to terminate an infinite loop")
            sandbox.compile(
                probe,
                '#include <iostream>\nint main(){while(true) std::cout << "1234567890";}',
                "output-probe-compile",
            )
            limited = sandbox.judge.execute(probe, "", 1000, 128, output_limit=4096)
            if not limited.output_exceeded or len(limited.stdout.encode()) > 4096:
                raise RuntimeError("Sandbox output cap failed")
            listed = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    f"label=minioj.owner={sandbox.owner}",
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            if listed.stdout.strip():
                raise RuntimeError("Importer leaked a Docker container")
            print(
                "CF importer smoke passed: 12 reproducible tests, dry-run, HTTP upload, Worker AC, "
                "LLM checker fixture/alternative AC/wrong WA, repeat skip, "
                "secret/filesystem/network isolation, timeout/output cap, cleanup."
            )
        finally:
            worker_main.running = False
            server.should_exit = True
            worker.join(timeout=20)
            web.join(timeout=10)
            sock.close()
            if worker.is_alive() or web.is_alive():
                raise RuntimeError("Isolated smoke services failed to stop")


if __name__ == "__main__":
    main()
