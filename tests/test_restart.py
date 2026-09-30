import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_restart(
    tmp_path,
    *,
    mode="nat",
    gateway="172.29.0.1",
    proxy=None,
    builder="stale",
    failure="",
):
    project = Path(__file__).resolve().parents[1]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.jsonl"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["RESTART_TEST_LOG"], "a") as stream:
    stream.write(json.dumps({{"args": args, "cwd": os.getcwd(), "env": {{key: os.environ.get(key) for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]}}}}) + "\\n")
state = os.environ["RESTART_TEST_BUILDER"]
failure = os.environ["RESTART_TEST_FAILURE"]
if (failure == "bootstrap" and "--bootstrap" in args) or args[:2] == ["compose", failure]:
    sys.exit(7)
if args[:2] == ["buildx", "inspect"]:
    if state == "absent" and "--bootstrap" not in args:
        sys.exit(1)
    driver = "docker" if state == "unexpected" else "docker-container"
    print("Name: minioj-builder\\nDriver: " + driver + "\\nName: minioj-builder0")
elif args[:1] == ["inspect"]:
    proxy = os.environ["HTTP_PROXY"] if state == "matching" else "http://127.0.0.1:7897"
    print("host\\nHTTP_PROXY=" + proxy + "\\nHTTPS_PROXY=" + proxy + "\\nNO_PROXY=localhost,127.0.0.1")
""")
    fake_docker.chmod(0o755)
    for command, output in {
        "wslinfo": mode,
        "uname": "6.6.87.2-microsoft-standard-WSL2",
        "ip": f"default via {gateway} dev eth0" if gateway else "",
    }.items():
        executable = fake_bin / command
        executable.write_text(f"#!{sys.executable}\nprint({output!r})\n")
        executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "RESTART_TEST_LOG": str(log),
        "RESTART_TEST_BUILDER": builder,
        "RESTART_TEST_FAILURE": failure,
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
    calls = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )
    return result, calls, project


@pytest.mark.parametrize(
    "mode,gateway,host",
    [
        ("nat", "172.29.0.1", "172.29.0.1"),
        ("nat", "172.30.16.1", "172.30.16.1"),
        ("mirrored", "172.29.0.1", "127.0.0.1"),
        ("", "172.29.0.1", "172.29.0.1"),
    ],
)
@pytest.mark.parametrize("proxy", [None, "http://172.29.0.1:9999"])
def test_network_detection_and_build_proxy(tmp_path, mode, gateway, host, proxy):
    result, calls, project = run_restart(
        tmp_path, mode=mode, gateway=gateway, proxy=proxy, builder="absent"
    )
    assert result.returncode == 0, result.stderr
    expected = proxy or f"http://{host}:7897"
    for call in calls:
        assert call["cwd"] == str(project)
        for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
            assert call["env"][key] == expected
    create = next(
        call["args"] for call in calls if call["args"][:2] == ["buildx", "create"]
    )
    assert f"env.HTTP_PROXY={expected}" in create
    assert f"env.HTTPS_PROXY={expected}" in create
    driver_options = [
        next(csv.reader([create[index + 1]]))
        for index, arg in enumerate(create)
        if arg == "--driver-opt"
    ]
    assert ["env.NO_PROXY=localhost,127.0.0.1"] in driver_options
    assert all("=" in field for fields in driver_options for field in fields)
    build = next(
        call["args"] for call in calls if call["args"][:2] == ["compose", "build"]
    )
    assert build[2:4] == ["--builder", "minioj-builder"]
    assert "--build-arg" in build and "HTTPS_PROXY" in build
    assert calls[-2]["args"] == [
        "compose",
        "up",
        "-d",
        "--no-build",
        "--wait",
        "--wait-timeout",
        "60",
    ]
    assert calls[-1]["args"] == ["compose", "restart", "nginx"]


@pytest.mark.parametrize("builder", ["stale", "matching", "absent", "unexpected"])
def test_builder_refresh_preserves_cache_and_checks_ownership(tmp_path, builder):
    result, calls, _ = run_restart(tmp_path, builder=builder)
    commands = [call["args"] for call in calls]
    removed = [args for args in commands if args[:2] == ["buildx", "rm"]]
    assert removed == (
        [["buildx", "rm", "--keep-state", "minioj-builder"]]
        if builder == "stale"
        else []
    )
    if builder == "unexpected":
        assert result.returncode == 1
        assert not any(args[:1] == ["compose"] for args in commands)
    else:
        assert result.returncode == 0, result.stderr
        created = any(args[:2] == ["buildx", "create"] for args in commands)
        assert created == (builder != "matching")


@pytest.mark.parametrize("failure", ["bootstrap", "build", "up"])
def test_failed_preparation_or_build_does_not_restart_nginx(tmp_path, failure):
    result, calls, _ = run_restart(tmp_path, failure=failure)
    assert result.returncode == 7
    assert not any(call["args"] == ["compose", "restart", "nginx"] for call in calls)
    if failure != "up":
        assert not any(call["args"][:2] == ["compose", "up"] for call in calls)


def test_missing_gateway_stops_before_docker(tmp_path):
    result, calls, _ = run_restart(tmp_path, gateway="")
    assert result.returncode == 1
    assert "Cannot detect the WSL NAT gateway" in result.stderr
    assert calls == []


def test_explicit_proxy_does_not_require_gateway(tmp_path):
    result, _, _ = run_restart(tmp_path, gateway="", proxy="http://172.29.0.1:7897")
    assert result.returncode == 0, result.stderr
