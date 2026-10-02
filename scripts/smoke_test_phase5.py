"""Isolated offline backup -> restore -> real HTTP Reference Client -> Docker Judge."""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from backup_restore import backup, restore

SENTINEL = "MINIOJ_HIDDEN_SENTINEL_93AF72"
SOURCES = {
    "WA": "#include <iostream>\nint main(){std::cout << 0;}",
    "CE": "int main( {",
    "RE": "int main(){return 23;}",
    "TLE": "int main(){for(;;){}}",
    "MLE": "int main(){volatile unsigned char* p = new unsigned char[256*1024*1024]; for(int i=0;i<256*1024*1024;i+=4096)p[i]=1;}",
    "OLE": "#include <iostream>\n#include <string>\nint main(){for(;;)std::cout<<std::string(65536,'x');}",
}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def ready(url, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Isolated HTTP server stopped before readiness")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("Isolated HTTP server readiness timeout")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-verdicts", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    owner = "phase5-" + secrets.token_hex(6)
    with tempfile.TemporaryDirectory(prefix="minioj-phase5-restore-") as directory:
        root = Path(directory)
        source = root / "source"
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("MINIOJ_", "OJ_"))
        }
        environment.update(
            {
                "MINIOJ_SECRET_KEY": secrets.token_urlsafe(48),
                "MINIOJ_DATABASE_URL": f"sqlite:///{source / 'database/oj.db'}",
                "MINIOJ_DATA_DIR": str(source / "data"),
                "MINIOJ_JOB_DIR": str(source / "jobs"),
                "MINIOJ_WORKER_OWNER": owner,
                "MINIOJ_FEEDBACK_POLICY": "full",
                "MINIOJ_DOCKER_IMAGE": os.getenv(
                    "MINIOJ_DOCKER_IMAGE", "minioj-cpp20:latest"
                ),
            }
        )
        os.environ.update(environment)
        # Fixture setup is server-side. The separate client below has no internal imports/files.
        from minioj.database import SessionLocal, engine, init_db
        from minioj.models import ApiToken, Problem, Submission, User
        from minioj.problems import add_testcase
        from minioj.security import create_api_token, hash_password

        init_db()
        token_id, token, digest, expires = create_api_token()
        with SessionLocal() as db:
            user = User(
                username="RestoreUser",
                email="restore@example.com",
                password_hash=hash_password("isolated-restore-password"),
            )
            db.add(user)
            db.flush()
            db.add(
                ApiToken(
                    id=token_id,
                    user_id=user.id,
                    name="isolated-reference",
                    token_hash=digest,
                    expires_at=expires,
                )
            )
            problem = Problem(
                id="phase5-sum",
                title="Restored Sum",
                statement="Add two integers.",
                created_by=user.id,
                time_limit_ms=1000,
                memory_limit_mb=128,
                created_at=datetime(2000, 1, 1, tzinfo=UTC),
            )
            db.add(problem)
            db.add_all(
                [
                    Problem(
                        id=f"restore-{i:03}",
                        title="Discovery fixture",
                        statement="Public",
                        created_by=user.id,
                    )
                    for i in range(55)
                ]
            )
            db.commit()
            add_testcase(db, problem, "sample", "7 8\n", "15\n")
            hidden = add_testcase(db, problem, "hidden", "20 22\n" + SENTINEL, "42\n")
            hidden_path = source / "data" / hidden.input_path
            db.add(
                Submission(
                    user_id=user.id,
                    problem_id=problem.id,
                    problem_revision=problem.revision,
                    source_code="historical source",
                    status="FINISHED",
                    verdict="AC",
                    compile_result='{"success":true}',
                    judge_result='{"verdict":"AC","summary":"Historical result"}',
                )
            )
            db.commit()
        engine.dispose()
        # No Web/Worker/writer has been started for this fixture. Copy all persistent files.
        snapshot = backup(
            source / "database",
            source / "data",
            root / "snapshot",
            writers_stopped=True,
        )
        restored = restore(snapshot, root / "restored")
        environment.update(
            {
                "MINIOJ_DATABASE_URL": f"sqlite:///{restored / 'database/oj.db'}",
                "MINIOJ_DATA_DIR": str(restored / "data"),
                "MINIOJ_JOB_DIR": str(restored / "jobs"),
            }
        )
        worker = server = None
        with (
            (root / "worker.log").open("w") as worker_log,
            (root / "server.log").open("w") as server_log,
        ):
            try:
                worker = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "minioj.worker.main",
                        "--poll-interval",
                        "0.05",
                    ],
                    cwd=project,
                    env=environment,
                    stdout=worker_log,
                    stderr=subprocess.STDOUT,
                )
                for prefix in ("", "/minioj"):
                    port = free_port()
                    server_env = {**environment, "MINIOJ_ROOT_PATH": prefix}
                    server = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "minioj.server.main:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(port),
                            "--no-access-log",
                        ],
                        cwd=project,
                        env=server_env,
                        stdout=server_log,
                        stderr=subprocess.STDOUT,
                    )
                    base = f"http://127.0.0.1:{port}{prefix}"
                    ready(base + "/healthz", server)
                    client_env = {
                        **environment,
                        "OJ_BASE_URL": base,
                        "OJ_API_TOKEN": token,
                        "OJ_PROBLEM_ID": "phase5-sum",
                    }
                    subprocess.run(
                        [
                            sys.executable,
                            str(project / "scripts/smoke_test_codeharness_api.py"),
                            "--expect-verdict",
                            "AC",
                        ],
                        env=client_env,
                        cwd=project,
                        check=True,
                        timeout=120,
                    )
                    if prefix and args.all_verdicts:
                        for verdict, code in SOURCES.items():
                            subprocess.run(
                                [
                                    sys.executable,
                                    str(
                                        project
                                        / "scripts/smoke_test_codeharness_api.py"
                                    ),
                                    "--expect-verdict",
                                    verdict,
                                ],
                                env={**client_env, "OJ_SOURCE_CODE": code},
                                cwd=project,
                                check=True,
                                timeout=120,
                            )
                        # Corrupt only the restored copy: the backup and source remain intact.
                        restored_hidden = (
                            restored / "data" / hidden_path.relative_to(source / "data")
                        )
                        restored_hidden.write_text("corrupt fixture", encoding="utf-8")
                        subprocess.run(
                            [
                                sys.executable,
                                str(project / "scripts/smoke_test_codeharness_api.py"),
                                "--expect-verdict",
                                "IE",
                            ],
                            env=client_env,
                            cwd=project,
                            check=True,
                            timeout=120,
                        )
                    stop(server)
                    server = None
                print(
                    "Phase 5 offline backup/restore HTTP and real Docker verdict smoke passed."
                )
            finally:
                stop(server)
                stop(worker)
        if any((restored / "jobs").glob("judge-*")) or any(
            (restored / "jobs").glob("metrics-*")
        ):
            raise RuntimeError("Isolated judge job directories were not cleaned")
        remaining = subprocess.check_output(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=minioj.owner={owner}",
            ],
            text=True,
        ).strip()
        if remaining:
            raise RuntimeError("Isolated Worker-owned containers were not cleaned")
        # Prove the backup can still restore, even after the runtime copy changed.
        restore(snapshot, root / "restored-again")
        print(
            "Temporary processes, Worker containers, jobs and directories cleaned; original database was not used."
        )


if __name__ == "__main__":
    main()
