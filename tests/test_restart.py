import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("up_status", [0, 7])
@pytest.mark.parametrize("proxy", [None, "http://127.0.0.1:9999"])
def test_restart_sets_proxy_and_only_restarts_nginx_after_success(
    tmp_path, up_status, proxy
):
    project = Path(__file__).resolve().parents[1]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.jsonl"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(f"""#!{sys.executable}
import json, os, sys
with open(os.environ["RESTART_TEST_LOG"], "a") as stream:
    stream.write(json.dumps({{"args": sys.argv[1:], "cwd": os.getcwd(), "env": {{key: os.environ.get(key) for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]}}}}) + "\\n")
if sys.argv[1:3] == ["compose", "up"]:
    sys.exit(int(os.environ["RESTART_TEST_STATUS"]))
""")
    fake_docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "RESTART_TEST_LOG": str(log),
        "RESTART_TEST_STATUS": str(up_status),
    }
    env.pop("MINIOJ_RESTART_PROXY", None)
    if proxy:
        env["MINIOJ_RESTART_PROXY"] = proxy
    result = subprocess.run(
        [str(project / "restart")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == up_status, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0]["args"] == ["compose", "up", "-d", "--build"]
    assert len(calls) == (1 if up_status else 2)
    if not up_status:
        assert calls[1]["args"] == ["compose", "restart", "nginx"]
    for call in calls:
        assert call["cwd"] == str(project)
        for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
            assert call["env"][key] == (proxy or "http://127.0.0.1:7897")
        for key in ["NO_PROXY", "no_proxy"]:
            assert call["env"][key] == "localhost,127.0.0.1"
