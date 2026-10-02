from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Isolated Docker CPU timing and supervisor checks"
    )
    parser.add_argument(
        "--image", default=os.getenv("MINIOJ_DOCKER_IMAGE", "minioj-cpp20:latest")
    )
    parser.add_argument(
        "--baseline-image", help="Optional old image for host-wall timing comparison"
    )
    args = parser.parse_args()
    owner = f"timing-smoke-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="minioj-timing-") as temporary:
        root = Path(temporary)
        os.environ["MINIOJ_DATA_DIR"] = str(root / "data")
        os.environ["MINIOJ_JOB_DIR"] = str(root / "jobs")
        os.environ["MINIOJ_DOCKER_IMAGE"] = args.image
        from minioj.config import settings
        from minioj.judge import DockerJudge

        settings.jobs_dir.mkdir(parents=True)
        job = root / "program"
        job.mkdir()
        judge = DockerJudge(args.image, owner=owner)
        judge.ensure_available()
        try:

            def compile_source(source):
                result = judge.compile(job, source, 128)
                if result.exit_code or result.timed_out or result.output_exceeded:
                    raise RuntimeError(
                        f"Timing test compilation failed: {result.stderr}"
                    )

            compile_source('#include <cstdio>\nint main(){ puts("ok"); }')
            samples = [judge.execute(job, "", 200, 128) for _ in range(6)]
            if any(
                r.stdout != "ok\n" or r.exit_code or r.timed_out or r.time_ms >= 50
                for r in samples
            ):
                raise RuntimeError(f"Constant program timing check failed: {samples}")
            report = {
                "constant_cpu_ms": [r.time_ms for r in samples],
                "constant_in_container_wall_ms": [r.wall_time_ms for r in samples],
            }
            if args.baseline_image:
                old = DockerJudge(args.baseline_image, owner=owner)
                previous = []
                for _ in range(6):
                    name = "minioj-baseline-" + uuid.uuid4().hex
                    command = old._container_command(
                        name, 128, str(job), ["/work/main"], read_only_mount=True
                    )
                    previous.append(
                        old._run_limited(command, name, "", 5000, 1024).time_ms
                    )
                report["old_host_wall_ms"] = previous

            compile_source(r"""
#include <time.h>
#include <unistd.h>
#include <cstdio>
long long cpu() { timespec t{}; clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t); return t.tv_sec*1000000000LL+t.tv_nsec; }
int main(){ usleep(300000); auto start=cpu(); while(cpu()-start<80000000){} puts("ok"); }
""")
            unloaded = [judge.execute(job, "", 200, 128) for _ in range(3)]
            stress = [
                subprocess.Popen(
                    [sys.executable, "-c", "while True: pass"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(4)
            ]
            try:
                loaded = [judge.execute(job, "", 200, 128) for _ in range(3)]
            finally:
                for process in stress:
                    process.terminate()
                for process in stress:
                    process.wait(timeout=5)
            for result in unloaded + loaded:
                if (
                    result.exit_code
                    or result.timed_out
                    or not 75 <= result.time_ms <= 150
                    or result.wall_time_ms < 300
                ):
                    raise RuntimeError(f"CPU/wall separation check failed: {result}")
            report["sleep_and_cpu_idle_ms"] = [r.time_ms for r in unloaded]
            report["sleep_and_cpu_loaded_ms"] = [r.time_ms for r in loaded]
            report["sleep_and_cpu_wall_ms"] = [
                r.wall_time_ms for r in unloaded + loaded
            ]
            report["load_median_change_ms"] = statistics.median(
                r.time_ms for r in loaded
            ) - statistics.median(r.time_ms for r in unloaded)

            compile_source(r"""
#include <sys/wait.h>
#include <unistd.h>
int main(){ if(fork()==0){ for(;;){ asm volatile(""); } } wait(nullptr); }
""")
            descendant = judge.execute(job, "", 200, 128)
            if not descendant.timed_out or not 195 <= descendant.time_ms <= 300:
                raise RuntimeError(f"Descendant CPU limit check failed: {descendant}")
            report["descendant_tle_cpu_ms"] = descendant.time_ms

            compile_source(r"""
#include <sys/types.h>
#include <sys/stat.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <fstream>
#include <string>
#include <cstdio>
int main(){
  if(getuid()!=1000 || geteuid()!=1000) return 1;
  std::ifstream status("/proc/self/status"); std::string line;
  while(std::getline(status,line)) if(line.rfind("CapEff:",0)==0 && line.find("0000000000000000")==std::string::npos) return 2;
  if(open("/minioj-result.json", O_WRONLY)>=0 || chmod("/minioj-result.json",0666)==0 || kill(1,SIGKILL)==0) return 3;
  if(fork()==0){setsid(); for(;;){asm volatile("");}}
  puts("safe");
}
""")
            safety = judge.execute(job, "", 1000, 128)
            if safety.exit_code or safety.stdout != "safe\n" or safety.timed_out:
                raise RuntimeError(f"Supervisor isolation check failed: {safety}")
            report["supervisor_and_detached_child"] = "passed"
            print(json.dumps(report, indent=2))
        finally:
            judge.cleanup_owned_containers()
        if list(settings.jobs_dir.iterdir()):
            raise RuntimeError("CPU metric job directories were not cleaned")
    print(
        "CPU timing, host-load, descendant limits and protected-report checks passed."
    )


if __name__ == "__main__":
    main()
