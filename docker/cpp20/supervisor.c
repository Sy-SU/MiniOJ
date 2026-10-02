#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/capability.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Trusted PID 1. Only this process can write the separately mounted report.
 * The submitted program runs as 1000:1000, without capabilities or report FDs.
 * cpu.stat accounts for every descendant, including detached/forked children. */
static long long cpu_usec(void) {
    FILE *stream = fopen("/sys/fs/cgroup/cpu.stat", "r");
    if (!stream) return -1;
    char key[128];
    long long value, result = -1;
    while (fscanf(stream, "%127s %lld", key, &value) == 2) {
        if (!__builtin_strcmp(key, "usage_usec")) { result = value; break; }
    }
    fclose(stream);
    return result;
}

static long long clock_ms(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (long long)value.tv_sec * 1000 + value.tv_nsec / 1000000;
}

static long long memory_kb(void) {
    FILE *stream = fopen("/sys/fs/cgroup/memory.peak", "r");
    long long value = -1;
    if (stream) { if (fscanf(stream, "%lld", &value) != 1) value = -1; fclose(stream); }
    return value < 0 ? -1 : value / 1024;
}

int main(int argc, char **argv) {
    if (argc < 4 || getuid() != 0 || getpid() != 1) return 125;
    long long limit = atoll(argv[1]), wall_limit = atoll(argv[2]);
    if (limit <= 0 || wall_limit <= 0) return 125;
    int report = open("/minioj-result.json", O_WRONLY | O_TRUNC | O_NOFOLLOW);
    if (report < 0 || fchown(report, 0, 0) || fchmod(report, 0644)) return 125;
    long long initial_cpu = cpu_usec(), started = clock_ms();
    if (initial_cpu < 0) { dprintf(2, "MiniOJ requires cgroup v2 CPU accounting.\n"); return 125; }
    struct rlimit core = {0, 0};
    if (setrlimit(RLIMIT_CORE, &core)) return 125;
    int errors[2];
    if (pipe2(errors, O_CLOEXEC)) return 125;
    pid_t child = fork();
    if (child < 0) return 125;
    if (child == 0) {
        close(report);
        close(errors[0]);
        /* Prefer killing the untrusted child rather than its small supervisor. */
        int score = open("/proc/self/oom_score_adj", O_WRONLY);
        if (score >= 0) { (void)write(score, "500", 3); close(score); }
        struct __user_cap_header_struct header = {_LINUX_CAPABILITY_VERSION_3, 0};
        struct __user_cap_data_struct caps[2] = {{0}, {0}};
        if (setgroups(0, NULL) || setgid(1000) || setuid(1000) ||
            syscall(SYS_capset, &header, caps) || prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) {
            int error = errno; (void)write(errors[1], &error, sizeof(error)); _exit(125);
        }
        execvp(argv[3], &argv[3]);
        int error = errno; (void)write(errors[1], &error, sizeof(error)); _exit(125);
    }
    close(errors[1]);
    int status = 0, timed_out = 0, setup_error = 0;
    long long used = 0;
    for (;;) {
        pid_t done = waitpid(child, &status, WNOHANG);
        long long cpu = cpu_usec();
        if (cpu < initial_cpu || done < 0) { setup_error = 1; break; }
        used = cpu - initial_cpu;
        if (done == child) break;
        if (used >= limit * 1000 || clock_ms() - started >= wall_limit) {
            timed_out = 1;
            (void)kill(-1, SIGKILL);
            while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
            break;
        }
        struct timespec delay = {0, 2000000};
        nanosleep(&delay, NULL);
    }
    /* PID namespace confines kill(-1). Reap even descendants that call setsid. */
    (void)kill(-1, SIGKILL);
    while (waitpid(-1, NULL, 0) > 0 || errno == EINTR) {}
    int child_error;
    if (read(errors[0], &child_error, sizeof(child_error)) > 0) setup_error = 1;
    close(errors[0]);
    long long final_cpu = cpu_usec();
    if (final_cpu >= initial_cpu) used = final_cpu - initial_cpu;
    else setup_error = 1;
    if (used >= limit * 1000) timed_out = 1;
    int exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    long long peak = memory_kb();
    char memory[64];
    if (peak < 0) snprintf(memory, sizeof(memory), "null");
    else snprintf(memory, sizeof(memory), "%lld", peak);
    dprintf(report, "{\"cpu_time_ms\":%lld,\"wall_time_ms\":%lld,\"timed_out\":%s,"
            "\"exit_code\":%d,\"memory_kb\":%s,\"setup_error\":%s}\n",
            (used + 999) / 1000, clock_ms() - started, timed_out ? "true" : "false",
            exit_code, memory, setup_error ? "true" : "false");
    close(report);
    return exit_code;
}
