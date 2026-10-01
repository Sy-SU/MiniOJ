# MiniOJ

[English](README.md) | 简体中文

MiniOJ 是一个面向浏览器用户和 Coding Agent 的轻量级多用户在线评测系统。它运行在 WSL Ubuntu 上，使用 SQLite 保存应用数据，并且只在受限的 Docker 容器中编译、执行不可信的 C++20 程序。

## 已实现功能

- 用户注册、登录、退出和签名 Session
- `user` / `admin` 两级角色与后端权限检查
- 使用 Argon2 存储密码哈希
- 只在创建时显示一次的 Agent API Token；数据库仅存 Token 摘要
- 题面、公开样例和文件系统中的隐藏测试数据
- 浏览器代码编辑、独立 Run Sample／Custom Test／Submit、提交历史和自动更新结果
- 版本化 REST API、Agent 专用题目数据和结构化 Judge Feedback
- 与 HTTP Server 分离的 Judge Worker
- 禁用网络并限制 CPU、内存、PID、权限、执行时间和输出量的 Docker Sandbox
- C++20 判题，以及 AC、WA、CE、RE、TLE、基础 MLE、OLE、IE Verdict

V1 明确不包含 Contest、排行榜、OAuth、Special Judge、Interactive Problem、Redis 和多语言 Judge。

## 架构

```text
浏览器 ── Session Cookie ─┐
                         ├── FastAPI Server ── SQLite
Agent ─── Bearer Token ──┘          │
                                    │ QUEUED Run／Build／Submission
                             独立 Judge Worker
                                    │
                             受限 Docker 容器
                                    │
                                C++20 程序
```

Server 不会直接执行用户二进制文件。Custom Run 在 HTTP 请求等待期间进入内部数据库队列，所有不可信执行都由独立 Worker 调用 Docker；用户代码容器只挂载当前 Job 目录。Docker Socket、OJ 数据库、WSL Home 和完整 Testcase 目录都不会挂载到用户程序容器。

## 环境要求

- Windows 的 WSL2
- Ubuntu 24.04 LTS
- Conda 或 Miniconda
- Docker Engine，或开启 WSL Integration 的 Docker Desktop
- 当前 Linux 用户能够执行 `docker` 命令

## 安装 Docker Engine

下面的命令适用于 Ubuntu 24.04，使用 [Docker 官方 apt 仓库](https://docs.docker.com/engine/install/ubuntu/)。

### 1. 添加官方仓库

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
```

### 2. 安装并启动 Docker

```bash
sudo apt install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

sudo systemctl enable --now docker
sudo systemctl enable containerd
```

### 3. 允许当前用户执行 Docker

```bash
sudo usermod -aG docker "$USER"
```

执行后关闭所有 WSL 终端，并在 Windows PowerShell 中运行：

```powershell
wsl --shutdown
```

重新进入 Ubuntu，然后验证：

```bash
docker version
docker compose version
docker run --rm hello-world
```

> 注意：`docker` 组成员可以通过 Docker Daemon 获得主机 root 级权限。只应把可信用户加入该组。更多说明见 [Docker Linux 安装后配置](https://docs.docker.com/engine/install/linux-postinstall/)。

## 创建 Conda 环境

进入项目目录：

```bash
cd /home/susenyang/github-repos/MiniOJ
conda env create --file environment.yml
conda activate minioj
cp .env.example .env
```

如果 `minioj` 环境已经存在，使用：

```bash
conda env update --file environment.yml --prune
conda activate minioj
```

## 配置

启动前先编辑 `.env`。`MINIOJ_SECRET_KEY` 必须替换为至少 32 个字符、长期保持不变的随机值；Server 会拒绝示例占位值和过短值。可以用下面的命令生成并把输出粘贴到 `.env`：

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

MiniOJ 只读取进程环境变量，不会自行解析 `.env`。Docker Compose 会自动读取 `.env`，但只注入 `compose.yaml` 明确映射的变量。在宿主机运行 `minioj`、`uvicorn` 或 `minioj-worker` 时，每个新终端都要先执行：

```bash
set -a
. ./.env
set +a
```

修改 Secret 会使已有浏览器 Session 失效。

常用配置见 [.env.example](.env.example)：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `MINIOJ_SECRET_KEY` | 必须替换的占位值 | 至少 32 个字符的 Session 签名密钥 |
| `MINIOJ_DATABASE_URL` | `sqlite:///./database/oj.db` | 数据库连接 |
| `MINIOJ_DATA_DIR` | `./data` | Testcase 根目录 |
| `MINIOJ_JOB_DIR` | `MINIOJ_DATA_DIR/jobs` | 可独立设置的临时 Job 目录 |
| `MINIOJ_TESTCASE_FILE_LIMIT_BYTES` | `16777216` | 单个 Testcase 输入或输出文件的最大字节数 |
| `MINIOJ_TESTCASE_BUILD_TIME_LIMIT_MS` | `10000` | std 或 generator 单次运行时间上限 |
| `MINIOJ_TESTCASE_BUILD_MEMORY_MB` | `512` | std 或 generator 单次运行内存上限 |
| `MINIOJ_GENERATOR_MAX_CASES` | `50` | 单个 generator 任务最多生成的用例数 |
| `MINIOJ_DOCKER_IMAGE` | `minioj-cpp20:latest` | 判题镜像名称 |
| `MINIOJ_WORKER_OWNER` | `worker` | Worker 清理 Docker 容器使用的实例标签；并行隔离实例必须不同 |
| `MINIOJ_FEEDBACK_POLICY` | `full` | `full`、`diagnostic` 或 `verdict_only` |
| `MINIOJ_SESSION_HTTPS_ONLY` | `false` | HTTPS 部署时设为 `true` |
| `MINIOJ_SOURCE_LIMIT_BYTES` | `262144` | Source 最大字节数 |
| `MINIOJ_STDIN_LIMIT_BYTES` | `262144` | Custom Run 输入最大字节数 |
| `MINIOJ_OUTPUT_LIMIT_BYTES` | `1048576` | stdout 与 stderr 合并上限 |
| `MINIOJ_COMPILE_TIME_LIMIT_MS` | `30000` | C++ 编译时间上限 |
| `MINIOJ_COMPILE_MEMORY_MB` | `512` | C++ 编译最低内存上限；题目限制更高时取更高值 |
| `MINIOJ_TOKEN_DEFAULT_DAYS` | `90` | Token 默认有效天数 |
| `MINIOJ_MAX_QUEUED_SUBMISSIONS` | `1000` | 正式提交排队容量 |
| `MINIOJ_MAX_QUEUED_RUNS` | `16` | Custom Run 排队容量 |
| `MINIOJ_CUSTOM_RUN_WAIT_SECONDS` | `45` | 同步 Custom Run 等待 Worker 的最长秒数 |
| `MINIOJ_OVERLOAD_RETRY_AFTER_SECONDS` | `2` | HTTP 429 的 `Retry-After` 秒数 |
| `MINIOJ_HTTP_PORT` | `80` | Compose Nginx 对外端口 |

未设置 `MINIOJ_JOB_DIR` 时仍沿用现有的 `data/jobs` 布局。宿主机 Worker 可设为 `/tmp/minioj/jobs`，将临时编译和运行文件与需备份的 Testcase 分开。用于 Docker Bind Mount 的目录必须对 Docker Daemon 可见。Compose Server 通过 SQLite 排队 Custom Run，不需要 Docker；宿主 Worker 必须与 Server 共用数据库和 data。隔离部署还可用 `MINIOJ_DATABASE_DIR`／`MINIOJ_DATA_HOST_DIR` 覆盖宿主挂载目录。

## 初始化 MiniOJ

确保已经激活 Conda 环境：

```bash
conda activate minioj
cd /home/susenyang/github-repos/MiniOJ
set -a
. ./.env
set +a
```

构建 C++20 判题镜像：

```bash
docker build -t minioj-cpp20:latest docker/cpp20
```

网页和 API 注册只创建普通用户，用户名 `admin`（不区分大小写）以及配置的初始管理员用户名禁止公开注册。管理员必须使用下面的命令创建，创建后直接登录，不要再次注册。

初始化数据库并创建管理员：

```bash
minioj init-db
minioj create-admin
```

也可以用环境变量非交互创建初始管理员：

```bash
export MINIOJ_ADMIN_USERNAME=admin
export MINIOJ_ADMIN_EMAIL=admin@example.com
export MINIOJ_ADMIN_PASSWORD='replace-with-a-strong-password'
minioj create-admin \
  --username "$MINIOJ_ADMIN_USERNAME" \
  --email "$MINIOJ_ADMIN_EMAIL" \
  --password "$MINIOJ_ADMIN_PASSWORD"
```

## 启动服务

终端一：

```bash
conda activate minioj
cd /home/susenyang/github-repos/MiniOJ
set -a
. ./.env
set +a
uvicorn minioj.server.main:app --host 0.0.0.0 --port 8000 --reload
```

终端二：

```bash
conda activate minioj
cd /home/susenyang/github-repos/MiniOJ
set -a
. ./.env
set +a
minioj-worker
```

Worker 是前台常驻进程，会占用当前终端，每秒检查 Custom Run、TestcaseBuild 和 Submission。看到 `Worker ready` 和 `No queued work; waiting for Custom Runs, testcase builds, or submissions.` 表示已就绪，正在等待任务，并非卡住。请保持此终端运行；按 `Ctrl+C` 停止。启动时会在 `MINIOJ_JOB_DIR` 获取独占锁、安全终结上次中断的任务并清理本实例残留容器／Job 目录；使用同一 Job 目录的第二个 Worker 会被拒绝。

如需处理完当前队列后自动退出，可运行 `minioj-worker --once`（会处理三类队列，直到都为空）。启动日志也会显示数据库初始化和 Docker 判题镜像检查阶段，便于定位启动问题。

若要让 Worker 作为 WSL systemd user service 常驻，仓库提供的 unit 默认假设项目位于 `%h/github-repos/MiniOJ`、Conda 位于 `%h/miniconda3`；路径不同请先编辑：

```bash
mkdir -p ~/.config/systemd/user
cp deploy/minioj-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now minioj-worker
systemctl --user status minioj-worker
journalctl --user -u minioj-worker -f
```

unit 会读取项目 `.env`、失败后自动重启。WSL 必须启用 systemd；`sudo loginctl enable-linger "$USER"` 可让用户服务在没有交互登录时启动。Docker 必须先运行，当前用户必须有 Docker 权限。

访问：

- Web UI：<http://localhost:8000>
- OpenAPI 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/healthz>

如需通过本机的 Tailscale 地址访问 Nginx 反代，在 `.env` 中设置强随机 `MINIOJ_SECRET_KEY` 后运行：

```bash
docker compose up -d --build
```

访问 <http://100.95.57.121/minioj/>。只有 Nginx 对外监听端口，FastAPI 仅在 Compose 内部网络提供服务；Nginx 会保留 `/minioj/` 前缀及原始 Host／端口。如果主机地址或端口变化，请修改 `.env` 中的 `MINIOJ_HTTP_BIND`／`MINIOJ_HTTP_PORT`。

Docker 部署时，推荐在正在运行的 Server 容器内创建管理员，确保写入网页使用的同一数据库：

```bash
docker compose exec server minioj create-admin --username admin
```

按提示输入邮箱和至少 10 位的密码。成功后命令会打印数据库地址和登录路径；访问 <http://100.95.57.121/minioj/login> 登录。数据库通过 `./database:/app/database` 持久化，容器重建不会删除账号。

如使用 `.env` 自动初始化管理员，必须同时设置 `MINIOJ_ADMIN_USERNAME`、`MINIOJ_ADMIN_EMAIL` 和 `MINIOJ_ADMIN_PASSWORD`，然后运行 `docker compose up -d --no-build`。已有同名、同邮箱且启用的管理员会保留原密码；如果与普通账号或其他邮箱冲突，启动会明确报错，不会静默跳过或自动提权。

Judge Worker 仍建议直接在 WSL 中运行，使 Docker Bind Mount 使用相同的主机路径。生产环境应让 Worker 使用单独、严格受控的 Docker Daemon，不要把通用 Docker Socket 暴露给 Web Server。

### WSL 启动后的日常启动

只需执行一次下面的命令，让 Docker Daemon 随 WSL 的 systemd 启动：

```bash
sudo systemctl enable docker containerd
```

`compose.yaml` 已为 Nginx 和 FastAPI 配置 `restart: unless-stopped`。它们创建成功后，Docker Daemon 再次启动时通常会自动恢复。每次 Windows 或 WSL 重启后，也可以执行下面这组幂等命令，确保前端反向代理和后端 API 都已启动：

```bash
sudo systemctl start docker
cd /home/susenyang/github-repos/MiniOJ
docker compose up -d --no-build
docker compose ps
```

当 `server` 和 `nginx` 都显示为 `healthy` 后，访问：

- Web UI：<http://100.95.57.121/minioj/>
- 健康检查：<http://100.95.57.121/minioj/healthz>

`--no-build` 会直接使用已经构建好的镜像，适合日常启动。如果修改了源码或 Dockerfile，请在项目根目录执行：

```bash
./restart
```

`restart` 会识别 WSL 网络模式：NAT 模式使用当前 IPv4 默认网关（Windows 主机地址），镜像网络模式使用 `127.0.0.1`，默认代理端口为 `7897`。旧版 WSL 无法报告网络模式时，会回退到 NAT 网关。可用 `MINIOJ_RESTART_PROXY=http://主机地址:端口 ./restart` 手动覆盖自动检测。请保持 Windows 代理开启，并允许 WSL 访问。

脚本设置大小写两组 HTTP/HTTPS 代理后，会检查专用的 `minioj-builder` BuildKit 容器。代理变化时，通过 `docker buildx rm --keep-state` 保留缓存卷并重建构建器，然后显式使用它执行 `docker compose build`，同时把代理作为构建参数传给依赖安装步骤。构建成功后执行 `docker compose up -d --no-build --wait`，最后重启 Nginx；准备或构建失败时不会重启应用服务。不要与另一个使用 `minioj-builder` 的构建同时运行。

脚本不会修改 `.env`、当前终端环境或 Docker Daemon 配置。本地已缓存的 BuildKit 镜像可用于启动构建器，因此当前机器不依赖 Daemon 的旧代理完成重建；首次安装需要拉取 BuildKit 镜像，或直接使用 `docker pull` 时，仍需为 Docker Daemon 配置可用代理。缓存保留机制见 [Docker 官方说明](https://docs.docker.com/build/builders/drivers/docker-container/#cache-persistence)。

判题 Worker 不在 Compose 中，需要以前述 systemd user service 常驻，或在单独的 WSL 终端启动：

```bash
conda activate minioj
cd /home/susenyang/github-repos/MiniOJ
minioj-worker
```

普通重启不需要访问 Docker Hub，因此不要求 Windows 代理保持开启；只有首次构建、拉取镜像或重新构建时才需要可用的网络和 Docker 代理。

## 创建第一道题

1. 使用管理员账号登录。
2. 打开 **Admin → New problem**。
3. 填写题面与时间、内存限制。题号为 3–80 位 ASCII 字母、数字或连字符，可保留大小写。题面字段支持安全 CommonMark 和 KaTeX 数学公式：行内公式使用 `$...$`，独立成行的块级公式使用 `$$...$$`。保存前可使用 **Preview Markdown**；内嵌 HTML 会按文本显示，不会启用不安全链接协议。KaTeX 随应用本地提供，浏览器渲染不依赖外部 CDN。
4. 在编辑页的 **02 · Standard solution** 分区直接粘贴 C++20 标准答案（std），点击 **Save standard solution**；也可以上传 UTF-8 源码文件。保存后源码直接显示在编辑框中，可继续编辑，且仅 Admin 可见。std 与题面分别保存。
5. 上传 `sample` 或 `hidden` 输入文件，Worker 会运行 std 并把 stdout 保存为输出；也可以上传 C++20 generator，一次生成多个 `generated` Testcase。

Generator 每次运行时通过 `argv[1]` 接收 seed、通过 `argv[2]` 接收从 1 开始的用例序号；stdout 作为输入，再交给 std 计算输出。任务异步执行，因此必须保持 `minioj-worker` 运行，并刷新编辑页查看 `FINISHED` 或 `FAILED`。编译或运行失败不会创建 Testcase；一个 generator 批次要么全部保存，要么全部回滚。

`sample` 会显示在题面上；`hidden` 和 `generated` 只在服务端保存。每个生成的输入或输出默认最多 16 MiB，并记录 SHA-256；std 和 generator 源码受 `MINIOJ_SOURCE_LIMIT_BYTES` 限制。

已有安装更新后，需要重新构建／重启 Web，并重启宿主机上的 `minioj-worker`；启动时会自动执行兼容数据库升级。当前 Compose 部署可先运行 `./restart`，再重启独立运行的 Worker。

即使已有 Submission，管理员仍可修改题面、限制、std 和 Testcase。题目页显示修改警告；版本早于当前题目的提交在列表和详情中显示“题目已修改，结果可能不再对应当前题目”的警告。已有成绩保留，不自动重判。若排队中的提交在 Worker 读取数据前遇到题目变化，以 `IE` 和明确原因结束，用户可重新提交；已经读取完数据的评测使用读取时的数据继续完成。

警告右上角的 **×** 可关闭当前页面上的提示，刷新后会重新显示。编辑页用编号分区和快捷导航区分题面／限制、std、测试数据和构建历史；输入文件与 generator 构建各有独立面板。

删除题目后，题目从列表下架，不能再提交；原链接显示“题目已删除”，历史提交仍能查看并显示删除警告。采用软删除，数据库记录、测试文件和提交历史保留，题号不允许复用。删除同时取消未完成的 generator 任务；替换 std 后旧 std 任务的结果会被拒绝，同一 std 的多个排队任务仍可正常依次追加用例。

更新后需重启 Web 和 Worker，使两者使用一致的新规则；启动时自动补充版本和删除状态字段，不需要重建数据库。Compose 部署按已有 `./restart` 流程重建 Web 镜像，再单独重启宿主 Worker。

没有 Testcase 的题目可以保存，但其提交会得到 `IE`，避免错误地把不完整题目判为 AC。

## Agent API 快速开始

登录网页，在 **Settings → API tokens** 创建 Token，并立即保存显示的 Secret。它之后无法恢复。

```bash
export OJ_TOKEN='oj_replace_me'
```

获取为 Agent 清洗后的题目：

```bash
curl -H "Authorization: Bearer $OJ_TOKEN" \
  http://localhost:8000/api/v1/agent/problems/two-sum

curl -X POST http://localhost:8000/api/v1/runs \
  -H "Authorization: Bearer $OJ_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"language":"cpp20","source_code":"int main(){return 0;}","stdin":""}'
```

提交 C++20 代码：

```bash
curl -X POST http://localhost:8000/api/v1/submissions \
  -H "Authorization: Bearer $OJ_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"problem_id":"two-sum","language":"cpp20","source_code":"#include <iostream>\nint main(){return 0;}"}'
```

查询状态和结构化反馈：

```bash
curl -H "Authorization: Bearer $OJ_TOKEN" \
  http://localhost:8000/api/v1/submissions/1

curl -H "Authorization: Bearer $OJ_TOKEN" \
  http://localhost:8000/api/v1/agent/submissions/1/feedback
```

Agent 题目接口默认不返回 Rating、Tags、Editorial、历史解法或隐藏测试数据。

## 主要 API

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/v1/auth/register` | 注册用户 |
| `GET` | `/api/v1/me` | 查询当前身份 |
| `GET/POST` | `/api/v1/tokens` | 查询或创建自己的 Token |
| `DELETE` | `/api/v1/tokens/{id}` | 删除自己的 Token，立即失效 |
| `GET` | `/api/v1/problems` | 公开题目列表 |
| `GET` | `/api/v1/problems/{id}` | 公开题目详情 |
| `GET` | `/api/v1/agent/problems/{id}` | 清洗后的 Agent 题目数据 |
| `POST` | `/api/v1/runs` | 由 Worker Custom Run 队列支撑的同步响应 |
| `POST` | `/api/v1/submissions` | 创建 Submission，返回 `202` |
| `GET` | `/api/v1/submissions/{id}` | 查询稳定的状态和结果字段 |
| `GET` | `/api/v1/agent/submissions/{id}/feedback` | 获取结构化调试反馈 |

使用 Cookie 认证的 API 写操作必须携带 `X-CSRF-Token`；内置网页会自动处理。Bearer Token 请求不需要 CSRF Token。

Custom Run 使用 `source_code`；只传旧字段 `code` 时仍兼容，同时传入时两者必须相同。正式提交或 Custom Run 队列满时返回 HTTP 429 和 `Retry-After`；Judge 基础设施故障或 Custom Run 等待 Worker 超时返回脱敏 HTTP 503；成功创建 Submission 仍返回 202。本轮没有新增公开的 Custom Run 轮询路由。

## 数据目录

- SQLite：`database/oj.db`
- Testcase：`data/problems/<problem-id>/tests/`
- 临时 Job：`MINIOJ_JOB_DIR`，未设置时为 `MINIOJ_DATA_DIR/jobs`；每次运行结束后清理自己的目录
- 单个 Testcase 输入或输出文件上限：`MINIOJ_TESTCASE_FILE_LIMIT_BYTES`，默认 16777216 字节
- std／generator 单次运行限制：`MINIOJ_TESTCASE_BUILD_TIME_LIMIT_MS`，默认 10000 ms；`MINIOJ_TESTCASE_BUILD_MEMORY_MB`，默认 512 MiB
- 单个 generator 任务最多用例数：`MINIOJ_GENERATOR_MAX_CASES`，默认 50
- Source 上限：`MINIOJ_SOURCE_LIMIT_BYTES`，默认 262144 字节
- Custom Run 输入上限：`MINIOJ_STDIN_LIMIT_BYTES`，默认 262144 字节
- 编译限制：`MINIOJ_COMPILE_TIME_LIMIT_MS`，默认 30000 ms；`MINIOJ_COMPILE_MEMORY_MB`，默认 512 MiB，题目内存限制更高时取更高值
- stdout + stderr 合并上限：`MINIOJ_OUTPUT_LIMIT_BYTES`，默认 1048576 字节
- API Token 默认有效期：`MINIOJ_TOKEN_DEFAULT_DAYS`，默认 90 天
- Submission／Custom Run 排队容量：`MINIOJ_MAX_QUEUED_SUBMISSIONS`（1000）和 `MINIOJ_MAX_QUEUED_RUNS`（16）
- Custom Run 等待／过载重试提示：`MINIOJ_CUSTOM_RUN_WAIT_SECONDS`（45 秒）和 `MINIOJ_OVERLOAD_RETRY_AFTER_SECONDS`（2 秒）
- Worker Docker 清理标签：`MINIOJ_WORKER_OWNER`，默认 `worker`；并行隔离实例必须使用不同值

V1 每个 Job 目录只允许一个 Worker。CustomRun、Submission 和 TestcaseBuild 都使用条件更新领取 QUEUED 行，并由进程独占锁防止第二个本机 Worker 竞争。重启时，中断的 CustomRun／TestcaseBuild 变为 `FAILED`，中断的 `COMPILING`／`RUNNING` Submission 以安全 `IE` 终结，都不会自动重复执行；SQLite、单 Server 进程内的原子容量准入和单机执行仍是扩展边界。

## 开发与测试

快速检查不需要 Docker：

```bash
conda activate minioj
ruff format --check .
ruff check .
pytest
```

Docker Daemon 和判题镜像可用时，运行隔离的端到端检查：

```bash
python scripts/smoke_test_stack.py
python scripts/smoke_test_judge.py
python scripts/smoke_test_generator.py
python scripts/smoke_test_sandbox.py
python scripts/smoke_test_worker_failures.py
python -m playwright install chromium
python scripts/smoke_test_phase3_deploy.py
```

这些脚本分别覆盖临时数据库栈流程、全部 Verdict 及退出码／输出边界、真实 generator/std、网络／PID／宿主文件／只读根目录／清理隔离、Testcase 损坏或缺失和镜像不可用时的安全 IE，以及真实 Chromium 经隔离 Compose/Nginx + 宿主 Worker 的 `/minioj/` 流程。Phase 3 部署冒烟使用临时数据库／data、随机回环端口和独立 Compose project，验证登录、网页 Token、Run Sample、Custom Test、Submit／自动更新、AC/CE/TLE、Docker 故障 503 和清理，不会部署正式环境。

`pytest` 测试套件不需要 Docker，也不会执行不可信程序。Docker 冒烟脚本需要正在运行的 Docker Daemon 和 `minioj-cpp20:latest` 镜像；Phase 3 部署冒烟还需要先下载 Playwright Chromium。

## 账号输入限制

- 新用户名：3–10 位，仅允许英文字母 `A–Z`、`a–z`；去除首尾空白。公开注册仍禁止使用保留的管理员名称。已有账号仍可使用原用户名登录。
- 邮箱：去除首尾空白并转为小写，总长度最多 254 字符，`@` 前最多 64 字符；接受 ASCII 邮箱和带点的域名，支持 `+` 标签，不接受空白、控制字符、显示名称或带引号的地址。
- 密码：至少 10 字符，UTF-8 编码最多 1024 字节；允许空格、符号和中文，不截断、不去除首尾空白。注册和修改密码必须两次输入一致；登录和校验当前密码也会拦截超长输入。
- 账号相关 POST 请求体最多 16 KiB，超过返回 HTTP 413；无 `Content-Length` 的分块请求同样受限。代码提交不受此账号请求限制影响。
- 浏览器输入限制与服务端校验共同生效；数据库查询使用参数绑定，HTML 模板保留自动转义。
- Settings 生成 API Token 后可点击 **Copy**；若浏览器拒绝剪贴板访问，可手动选择并复制。完整 Token 仍只展示一次；列表显示实际密钥前 7 位和后 4 位，中间使用 8 个星号，例如 `oj_abcd********wxyz`。数据库只保留密钥哈希与此掩码摘要，启动时会自动补充摘要字段；旧 Token 无法从哈希恢复首尾，显示摘要不可用提示，仍可正常使用或删除。点击 **Delete** 会永久删除 Token 并立即使其失效；以前撤销的 Token 也可以删除。

## 安全提示

- 对外提供服务前必须配置高强度、持久化的 `MINIOJ_SECRET_KEY`。
- HTTPS 部署时设置 `MINIOJ_SESSION_HTTPS_ONLY=true`。
- 不要暴露未认证的 Docker TCP Socket。
- Docker 隔离能够降低风险，但面对真正恶意的公开评测负载时，仍建议使用专用主机或 VM，并进一步加固内核级 Sandbox。
- SQLite 数据库和 `data/problems` 必须一起备份，避免 Testcase Metadata 与文件不一致。
- Docker 发布端口可能绕过部分 UFW 规则；部署到公网前应阅读 Docker 官方防火墙说明。

## 常见问题

检查 Docker 服务：

```bash
systemctl status docker --no-pager
journalctl -u docker --no-pager -n 100
```

如果 WSL 没有运行 systemd，请在 `/etc/wsl.conf` 中启用：

```ini
[boot]
systemd=true
```

然后在 Windows PowerShell 中运行 `wsl --shutdown` 并重新进入 Ubuntu。

如果刚加入 `docker` 组后仍然出现 Socket 权限错误，请完全退出并重新进入 WSL，而不是使用 `sudo docker` 生成 root 所有的用户配置文件。
