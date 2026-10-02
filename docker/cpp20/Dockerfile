FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin judge

COPY supervisor.c /tmp/supervisor.c
RUN gcc -O2 -Wall -Wextra -Werror -o /usr/local/bin/minioj-supervisor /tmp/supervisor.c \
    && rm /tmp/supervisor.c
LABEL minioj.cpu-accounting="cgroup-v2-v1"

USER judge
WORKDIR /work
