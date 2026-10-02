# MiniOJ

[English](README.md) | 简体中文

MiniOJ 是一个面向浏览器用户和 Coding Agent 的轻量级多用户在线评测系统。它运行在 WSL Ubuntu 上，使用 SQLite 保存应用数据，并且只在受限的 Docker 容器中编译、执行不可信的 C++20 程序。

Release Candidate 状态（2026-10-02）：**MiniOJ V1 source RC complete**。Core／可选 CF 工具已分开提交并推送，测试夹具最小修复 `40ede95` 的 [hosted Checks CI](https://github.com/Sy-SU/MiniOJ/actions/runs/37008639747) **725 项全通过，0 failed／0 skipped**，Ruff format／lint、应用 JS 和示例 Compose 均通过。正式上线仍为 **READY_WITH_NOTES**，不是 production deployed；提交范围、历史镜像差异和旧备份跟踪备注见 [TODO.md](TODO.md)。本轮未修改正式数据库／服务／镜像或创建 tag，正式升级和发布标签需单独授权。

## 已实现功能

- 用户注册、登录、退出和签名 Session
- `user` / 内容 `admin` / `system` 三角色、集中能力与后端权限检查
- 独立管理 Console、提交筛选、单提交 Rejudge 与逐次 Judge History
- 公开 Contest、现有题目排序／报名／提交和动态 ICPC 排名
- 公开个人主页、当前有效 AC 去重过题列表、安全本地头像
- 使用 Argon2 存储密码哈希
- 只在创建时显示一次的 Agent API Token；数据库仅存 Token 摘要
- 题面、公开样例和文件系统中的隐藏测试数据
- 浏览器代码编辑、独立 Run Sample／Custom Test／Submit、提交历史和自动更新结果
- 版本化 REST API、Agent 专用题目数据和结构化 Judge Feedback
- 与 HTTP Server 分离的 Judge Worker
- 禁用网络并限制 CPU、内存、PID、权限、执行时间和输出量的 Docker Sandbox
- C++20 判题，以及 AC、WA、CE、RE、TLE、基础 MLE、OLE、IE Verdict

V1 已包含公开 Contest、ICPC 排名和单场 Performance，不包含用户永久 rating、冻结榜、私密比赛、OAuth、评分／交互题、Redis 和多语言 Judge；Polygon 扩展支持 C++ testlib Special Judge。

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

反馈模式在 OJ 根目录 `.env` 中调整：

```dotenv
MINIOJ_FEEDBACK_POLICY=full
```

可选 `full`（权限范围内的详细反馈）、`diagnostic`（安全、截断后的编译诊断与 metadata，不含用例内容）、`verdict_only`（结论、安全摘要及可用的失败序号）。`GET /api/v1/me` 的 `feedback_mode` 返回 Server 实际加载的模式，默认是 `full`；该字段不会提高账号权限。修改后，宿主 Server 需重新导入 `.env` 并重启；Compose 需重新创建 Server 容器以更新环境变量，单独 `docker compose restart server` 不会加载新值。

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
| `MINIOJ_OUTPUT_LIMIT_BYTES` | `8388608` | stdout 与 stderr 合并上限 |
| `MINIOJ_COMPILE_TIME_LIMIT_MS` | `30000` | C++ 编译时间上限 |
| `MINIOJ_COMPILE_MEMORY_MB` | `512` | C++ 编译最低内存上限；题目限制更高时取更高值 |
| `MINIOJ_CHECKER_TIME_LIMIT_MS` | `10000` | testlib checker 单次独立 CPU 时间预算，不计入选手成绩 |
| `MINIOJ_CHECKER_MEMORY_MB` | `512` | testlib checker 独立内存预算，不计入选手成绩 |
| `MINIOJ_TOKEN_DEFAULT_DAYS` | `90` | Token 默认有效天数 |
| `MINIOJ_MAX_QUEUED_SUBMISSIONS` | `1000` | 正式提交排队容量 |
| `MINIOJ_MAX_QUEUED_RUNS` | `16` | Custom Run 排队容量 |
| `MINIOJ_CUSTOM_RUN_WAIT_SECONDS` | `45` | 同步 Custom Run 等待 Worker 的最长秒数 |
| `MINIOJ_OVERLOAD_RETRY_AFTER_SECONDS` | `2` | HTTP 429 的 `Retry-After` 秒数 |
| `MINIOJ_POLYGON_ARCHIVE_LIMIT_BYTES` | `67108864` | Polygon ZIP 上传上限，默认 64 MiB |
| `MINIOJ_POLYGON_EXPANDED_LIMIT_BYTES` | `268435456` | Polygon ZIP 展开总量上限，默认 256 MiB |
| `MINIOJ_HTTP_PORT` | `80` | Compose Nginx 对外端口 |

未设置 `MINIOJ_JOB_DIR` 时仍沿用现有的 `data/jobs` 布局。宿主机 Worker 可设为 `/tmp/minioj/jobs`，将临时编译和运行文件与需备份的 Testcase 分开。用于 Docker Bind Mount 的目录必须对 Docker Daemon 可见。Compose Server 通过 SQLite 排队 Custom Run，不需要 Docker；宿主 Worker 必须与 Server 共用数据库和 data。隔离部署还可用 `MINIOJ_DATABASE_DIR`／`MINIOJ_DATA_HOST_DIR` 覆盖宿主挂载目录。

## 初始化 MiniOJ

运行计时要求 Docker 使用 cgroup v2。升级本轮代码后须重新构建 `docker/cpp20` 判题镜像，并在应用变更时重启 Worker；Worker 会拒绝缺少 CPU 监督进程标签的旧镜像。Server 重建不会自动更新判题镜像。

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

网页和 API 注册只创建普通用户，用户名 `admin`（不区分大小写）以及配置的初始管理员用户名禁止公开注册。兼容命名的 `create-admin`／`MINIOJ_ADMIN_*` 现在创建最高权限 `system` 引导账号，创建后直接登录；原有 admin 会在一次性迁移中保留系统能力。system 可在 **管理 → Users** 将其他账号设为内容 admin，不要通过公开注册指定角色。

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

如需使用 Nginx 反代，从公开示例复制 `.env` 并设置强随机 `MINIOJ_SECRET_KEY` 后运行：

```bash
docker compose up -d --build
```

公开 `.env.example` 现为 `MINIOJ_HTTP_BIND=127.0.0.1`，复制后仅本机访问 <http://127.0.0.1/minioj/>。需要远程／Tailscale 访问时，显式设置自己的绑定地址，并相应替换访问 URL。已有 `.env` 和 Compose 旧 fallback 没有更改，部署时务必显式配置该变量。只有 Nginx 对外监听端口，FastAPI 仅在 Compose 内部网络提供服务；Nginx 会保留 `/minioj/` 前缀及原始 Host／端口。端口变化请修改 `MINIOJ_HTTP_PORT`。

Docker 部署时，推荐在正在运行的 Server 容器内创建管理员，确保写入网页使用的同一数据库：

```bash
docker compose exec server minioj create-admin --username admin
```

按提示输入邮箱和至少 10 位的密码。成功后命令会打印数据库地址和登录路径；访问 <http://127.0.0.1/minioj/login>（或自己配置的主机）登录。数据库通过 `./database:/app/database` 持久化，容器重建不会删除账号。

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

## 已有安装升级（三角色、比赛与重判）

本次兼容升级新增 avatar_key、Submission 可空 contest_id／generation、JudgeRun 历史、比赛表和迁移标记，以及 Phase 5 可空请求摘要列和 user/key 唯一索引；旧提交摘要保持 null。`init-db` 一次性将旧 admin 改为 system，为每个旧提交回填 initial 历史；保留原账号、题目、源码、成绩和时间，不自动重判。重复执行不会再次提升新 admin。

不要将旧角色／Worker 代码与升级后的数据库混跑。准备实际升级时按下面顺序操作；本轮开发不会自动执行这些正式操作：

1. 停止宿主 Worker：使用提供的 unit 时执行 `systemctl --user stop minioj-worker`，否则正常停止终端进程；停止 Web 流量及进程，Compose 使用 `docker compose stop nginx server`。
2. 两个写入进程均停止后，备份完整数据库目录（包含可能存在的 SQLite WAL/SHM）、data 及配置，备份放仓库外。不要删除 `oj.db` 或执行 `down -v` 代替升级。
3. 用 `conda env update --file environment.yml --prune` 同步依赖，激活 minioj 并按上文加载 `.env`。核对宿主 `MINIOJ_DATABASE_URL`／`MINIOJ_DATA_DIR` 与 Compose 实际持久化目录相同，注意自定义 bind 路径；不要误升级另一份空库。
4. 执行两次 `minioj init-db`，核对账号／提交数量、旧管理员 role=system 及历史成绩，保留 `roles-v2` 标记。失败时不要启动进程，按备份整体恢复旧代码／数据库／data，不要手工删除标记。
5. `docker compose build server` 构建新版 Server，`docker compose up -d` 启动 Web/Nginx，再启动新版宿主 Worker。不能把旧镜像连接到升级库；验收 health、原管理员登录、`/manage/users`、历史结果及新练习／比赛提交。

新增 Pillow 为头像运行依赖；Server 和 Worker 必须使用同版代码。权限、比赛和重判规则分别见 [permissions](docs/permissions.md)、[contest](docs/contest.md)、[rejudge](docs/rejudge.md)。

## 管理后台、重判、主页与比赛

admin/system 登录后，顶部 MiniOJ 紧右侧显示红色 **管理**。`/manage` 使用独立深色 Sidebar 和浅色密集内容区；Dashboard 包含用户、题目、提交、比赛、今日提交、AC rate、运行中／未开始比赛、队列及最近记录。两类管理员可管理 Problems／Contests／Submissions，Users／System 仅 system；admin 手工访问也返回 403。旧 `/admin` 跳转到新 Console，旧题目管理 URL 仍兼容。system 不能自降级／禁用或移除最后一个活跃 system；系统页只读安全状态，不泄漏 secret。

在 **Submissions** 按 ID、用户、题目、比赛、语言、状态、verdict 筛选，进入终态提交详情，点击 **Rejudge** 并确认。原 id／源码／语言／创建时间不变，使用当前 testcase 入同一 Worker 队列，**Judge History** 保留每次触发者、时间和结果。正在评测返回 409，队列满 429，已删除题目不可重判；重判中不保留旧 AC 的统计，最新终态（包括 IE）成为当前结果，主页和排名动态更新。

`/users/{username}` 公开头像、加入日期、去重 solved、首次当前有效 AC 时间、提交／AC／通过率和最近提交 metadata，不公开 email、Token 或他人源码。**Settings → Avatar** 支持真实 PNG/JPEG/WebP，最大 1 MiB／4 百万像素，重编码到 256×256 内的 PNG，随机 key 存本地；无头像使用默认 SVG。用户名固定，原有 email／密码／Token 操作继续可用。

**管理 → Contests** 创建公开比赛，起止时间明确按 UTC 输入，以现有题号列表顺序生成 A/B/C（超过 Z 为 AA）。编辑页输入现有题号，点 **Add problem**，通过 **Remove／↑／↓** 调整，最后 **Save contest**；禁用 JavaScript 时逐行编辑题号框。admin/system 在已开始、已结束或已有提交时也可改题目列表，无额外确认限制。移除只删除比赛关联、保留题目和提交，排名不再计入；重新加入时比赛时间窗内的旧结果重新生效。开赛后只锁定时间，运行中仍不可删除整场比赛。

用户可显式报名，比赛提交也自动报名；比赛题目页使用 `POST /api/v1/contests/{id}/submissions`，请求体与普通 SubmissionCreate、202 响应相同，普通接口仍为 practice。仅 RUNNING 允许比赛提交。ICPC 排名按当前第一条 AC 解题，之前 WA/RE/TLE/MLE/OLE 每次罚 20 分钟，CE/IE 不罚时，同分同罚时共享名次。新增 **Performance** 为后端难度加权 logistic 单场估计（0–4000；空 rating 用 1200），不改变排名、不保存为用户永久 rating。刷新后使用最新题目列表、难度和当前评测；公开 `GET /api/v1/contests/{id}/standings` 返回同一 JSON 榜单。本次增量无需新增数据库迁移；算法／边界见 [contest](docs/contest.md)。未开始比赛的关联题目列表对普通用户隐藏，榜单按可见空列表计算并显示 Performance 0；原有练习题库仍公开，不承诺私密题库语义。

新功能回归（临时数据库／目录；真实重判冒烟需要 Docker）：

```bash
python -m pytest tests/test_management.py tests/test_management_migration.py tests/test_management_browser.py tests/test_contest_performance.py
python scripts/smoke_test_management.py
```

## 创建第一道题

1. 使用管理员账号登录。
2. 打开 **管理 → Problems → New problem**。
3. 填写题面与时间、内存限制。题号为 3–80 位 ASCII 字母、数字或连字符，可保留大小写。题面字段支持安全 CommonMark 和 KaTeX 数学公式：行内公式使用 `$...$`，独立成行的块级公式使用 `$$...$$`。保存前可使用 **Preview Markdown**；内嵌 HTML 会按文本显示，不会启用不安全链接协议。KaTeX 随应用本地提供，浏览器渲染不依赖外部 CDN。
4. 在编辑页的 **02 · Standard solution** 分区直接粘贴 C++20 标准答案（std），点击 **Save standard solution**；也可以上传 UTF-8 源码文件。保存后源码直接显示在编辑框中，可继续编辑，且仅 Admin 可见。std 与题面分别保存。
5. 上传 `sample` 或 `hidden` 输入文件，Worker 会运行 std 并把 stdout 保存为输出；也可以上传 C++20 generator，一次生成多个 `generated` Testcase。

Generator 每次运行时通过 `argv[1]` 接收 seed、通过 `argv[2]` 接收从 1 开始的用例序号；stdout 作为输入，再交给 std 计算输出。任务异步执行，因此必须保持 `minioj-worker` 运行，并刷新编辑页查看 `FINISHED` 或 `FAILED`。编译或运行失败不会创建 Testcase；一个 generator 批次要么全部保存，要么全部回滚。

`sample` 会显示在题面上；`hidden` 和 `generated` 只在服务端保存。每个生成的输入或输出默认最多 16 MiB，并记录 SHA-256；std 和 generator 源码受 `MINIOJ_SOURCE_LIMIT_BYTES` 限制。

已有安装本次首次更新请先按上面的“三角色、比赛与重判”协调升级步骤停止两端、备份、迁移，再启动新版。升级后日常重启可继续使用 `./restart`，并单独重启 Worker。

即使已有 Submission，管理员仍可修改题面、限制、std 和 Testcase。题目页及提交页面不再显示修改／删除警告；已有成绩保留，不自动重判。内部版本检查仍保留：若排队中的提交在 Worker 读取数据前遇到题目变化，以 `IE` 和明确原因结束，用户可重新提交；已经读取完数据的评测使用读取时的数据继续完成。

其他应用警告仍可用右上角的 **×** 关闭当前页面提示。题目修改／删除警告及浏览器持久化关闭功能已移除，无需清除站点存储。编辑页用编号分区和快捷导航区分题面／限制、std、测试数据和构建历史；输入文件与 generator 构建各有独立面板。

删除题目后，题目从列表下架，不能再提交；打开原题目链接时仅显示“题目不存在”（Problem not found）页面，沿用 HTTP 410。从未存在的题号显示相同页面并返回 HTTP 404。历史提交仍能正常查看，不显示修改／删除警告。采用软删除，数据库记录、测试文件和提交历史保留，题号不允许复用。删除同时取消未完成的 generator 任务；替换 std 后旧 std 任务的结果会被拒绝，同一 std 的多个排队任务仍可正常依次追加用例。

Web 和 Worker 必须使用一致的新规则；启动会幂等补列，不需要重建数据库。本次首次角色升级仍须采用上面的协调停机／备份／迁移流程，不能先改库再混跑旧进程。

没有 Testcase 的题目可以保存，但其提交会得到 `IE`，避免错误地把不完整题目判为 AC。

## 提交的逐数据点结果

打开提交详情，样例在 **Sample results**，非样例（hidden/generated）在 **Testcase results**，保留原始测试点编号，显示每点状态、运行时间（ms）和内存峰值（KB）。有权查看该提交的普通用户和 admin/system 都能看到所有点的状态／资源。在 `full` 模式下，点击可用的样例编号展开输入、标准答案及已保存的实际输出；只有 admin/system 能展开非样例。普通用户的页面不会包含非样例内容，不是仅隐藏按钮。空分组不展示，预览仍限前 1024 字节，题目版本变化后不把当前文件附到旧结果。

新评测使用 CPU 时间，历史数据保留原计时方式及原值。长测试集和手机页面可在各表格内滚动；评测完成后自动刷新。仍在首个失败点停止，后续点显示 **Not run**；缺失测量为 `—`，旧提交没有逐点记录时提示重新提交，不伪造状态／测量。`diagnostic` 保留所有点的 metadata 但禁止任何数据预览，`verdict_only` 隐藏两组表格。原提交所有权校验和共用 HTTP 字段不变。

## 源码预览、复制与编辑器快捷键

提交详情的源码使用本地轻量 C++20 高亮，区分关键字、注释、字符串、数字和预处理行；**Copy code** 复制完整纯文本源码，不复制着色 HTML。普通 HTTP 或 Clipboard API 权限拒绝时会尝试本地复制回退；两种方式均失败时给出提示，可手动选择源码复制。不新增 CDN 或源码访问权限。

练习／比赛题目页和管理页的 std 源码编辑器中，**Tab** 输入四个空格，选中多行时整体缩进；**Shift+Tab** 反缩进。按 **Esc，再按 Tab** 可恢复正常焦点导航，不会困在编辑器内。Custom input 和其他表单字段仍按普通 Tab 切换。提交请求与保存的源码保持原有规则。

测试数据预览最多显示前 **1024 个 UTF-8 字节**，超出时在末尾加 `...`，表示还有未显示内容；不足或恰好 1024 字节不额外加标记。标记只属于网页展示，不改保存数据、机器 Feedback 字符串或隐藏数据权限。

Server 镜像构建现排除运行时头像、题库数据、Job、数据库和 `.env`，已有 bind 数据不受影响。2026-10-02 已通过独立临时 BuildKit 从官方 Python 基础镜像无应用缓存构建、重新下载全部依赖，并以该镜像完成隔离部署检查；没有修改共享 Docker daemon 的失效代理。已单独配置可用网络的 builder 可通过 `--build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY` 传递构建代理；代理和凭据不写入镜像。

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
| `GET` | `/api/v1/me` | 查询当前身份和生效反馈模式 `feedback_mode` |
| `GET/POST` | `/api/v1/tokens` | 查询或创建自己的 Token |
| `DELETE` | `/api/v1/tokens/{id}` | 撤销自己的 Token、保留 metadata（`204`） |
| `GET` | `/api/v1/problems` | 公开题目列表 |
| `GET` | `/api/v1/problems/{id}` | 公开题目详情 |
| `GET` | `/api/v1/agent/problems/{id}` | 清洗后的 Agent 题目数据 |
| `POST` | `/api/v1/runs` | 由 Worker Custom Run 队列支撑的同步响应 |
| `POST` | `/api/v1/submissions` | 创建 Submission，返回 `202` |
| `GET` | `/api/v1/submissions/{id}` | 查询稳定的状态和结果字段 |
| `GET` | `/api/v1/agent/submissions/{id}/feedback` | 获取结构化调试反馈 |

使用 Cookie 认证的 API 写操作必须携带 `X-CSRF-Token`；内置网页会自动处理。Bearer Token 请求不需要 CSRF Token。

Custom Run 使用 `source_code`；只传旧字段 `code` 时仍兼容，同时传入时两者必须相同。正式提交或 Custom Run 队列满时返回 HTTP 429 和 `Retry-After`；Judge 基础设施故障或 Custom Run 等待 Worker 超时返回脱敏 HTTP 503；成功创建 Submission 仍返回 202。本轮没有新增公开的 Custom Run 轮询路由。

普通机器接口在 OpenAPI 中绑定显式 Pydantic 请求／响应模型。查询 Submission 时，约定字段始终存在：未完成时 `verdict`、`tests`、`resources` 及尚不可用的时间字段为 JSON `null`，不会省略。`time_ms` 单位为毫秒；`memory_kb` 为兼容保留的字段名，实际单位为 KiB，无法采集时为 `null`。

`started_at` 在 Worker 尚未领取时为 null，COMPILING／RUNNING 已可有值；`finished_at` 在结束前为 null。“未完成”不等于全部时间字段都为 null。

API 错误使用稳定包络，旧 `detail` 暂时保留给已有客户端；validation details 不回显密码、Token、源码或 stdin 原值：

```json
{
  "error": {
    "code": "validation_error",
    "message": "Unprocessable Entity",
    "details": [{"location": ["body", "language"], "message": "...", "type": "literal_error"}]
  },
  "detail": [{"location": ["body", "language"], "message": "...", "type": "literal_error"}]
}
```

`DELETE /api/v1/tokens/{id}` 是幂等软撤销：设置 `revoked_at`、立即禁用密钥，并保留 metadata／hash 供审计。Web 表单路径保持兼容，但按钮和提示统一为 **Revoke**。当前没有公开硬删除操作。

## CodeHarness v1 协议与题库分页

完整冻结契约见 [HTTP v1 contract](docs/codeharness-api.md)，覆盖认证、题目发现、分页／排序、提交、轮询、Feedback、错误及幂等。保持既有路径、HTTP 状态和 Phase 4 错误码，不实现 CodeHarness 内部逻辑。

网页 `/problems` 每页 **50 题**，提供 Previous／Next、页码、ID／题名搜索，以及 **Default／Difficulty ↑／Difficulty ↓**。应用搜索／排序回到第 1 页，翻页保留参数；两个难度方向均将 null rating 排末尾，同难度按 ID 升序，默认网页 ID 顺序／API 最新创建优先不变。查询在 SQL 层完成，根路径／`/minioj`、手机和无 JavaScript 都可使用。

`GET /api/v1/problems?page=1&sort=difficulty_asc` 保留原数组响应，通过 `X-Total-Count/X-Page/X-Page-Size/X-Total-Pages` 告知分页；不传 page 保持旧的全量行为，新客户端应总是显式传 page。客户端不能提高显式页大小；非法 page／sort 返回安全 422，越界为空，删除题目先过滤再统计。`q` 按字面子串匹配 ID／title，最多 200 字符。

Feedback 沿用 `/api/v1/agent/submissions/{id}/feedback`，不增重复接口。授权且存在时，全部未完成／八种最终 verdict 都是 **200**，显式 `FeedbackResponse` 的 `submission_id/status/verdict/feedback_mode/failed_test/compile/execution/diagnostic/summary/summary_truncated` 始终存在，不可用为 null。保留可选旧 failure/tests/resources 视图。Submission 查询仍用 `submission_id` 和嵌套 `resources.time_ms/memory_kb`，不改名或复制顶层字段；运行 CPU 毫秒、编译墙钟毫秒，memory_kb 实为 KiB／未知 null。

| Feedback 暴露内容 | full | diagnostic | verdict_only |
| --- | --- | --- | --- |
| status／verdict／模式、安全摘要、失败序号 | 是 | 是 | 是 |
| 安全编译诊断、资源和逐点 metadata | 是 | 是 | 否 |
| 样例失败数据预览 | 是 | 否 | 否 |
| hidden/generated 预览 | 仅 admin/system | 否 | 否 |
| 内部错误／路径／traceback／secret | 绝不 | 绝不 | 绝不 |

最终内容为模式白名单与原角色／所有权权限的交集；**full 不等于无限 hidden 权限**，query 不能提高服务器模式。公开文本统一最多 1024 UTF-8 字节并附明确截断标志，编译文本脱敏，摘要／diagnostic 用安全固定说明；内部保存结果不变。普通查询、网页及历史共用边界，verdict_only 查询 tests/resources 为 null。客户端仅依赖 status/verdict 和 error.code，不解析 summary、HTML 或自然语言日志。

普通提交 POST 可带 **Idempotency-Key**：1–128 个无空格的可打印 ASCII 字符。同 user/key/有效请求重试返回原 **202/QUEUED** 与相同 ID；不同有效请求为 409 `idempotency_conflict`。存摘要，数据库唯一约束和写锁保证并发安全；未带 key 的旧调用仍独立创建，比赛 POST 不新增该头。POST 超时不确定是否成功时，重用原 key／body；轮询超时不能重新提交。

独立 Reference Client 只使用标准库 HTTP／Bearer／JSON，不 import MiniOJ、不读取数据库／题库／testcase。默认源码仅解两个整数求和；其它题通过 `OJ_SOURCE_CODE` 提供自己的代码。以下会在选定实例上创建提交：

```bash
export OJ_BASE_URL='http://localhost:8000'  # 或 http://localhost/minioj/
export OJ_API_TOKEN='oj_replace_me'
export OJ_PROBLEM_ID='sum-two'
export OJ_IDEMPOTENCY_KEY='save-a-unique-request-key'
python scripts/smoke_test_codeharness_api.py --timeout 120 --expect-verdict AC
```

流程为 **Me → 分页发现 → 清洗题目 → Submission → Poll → Feedback**。轮询按 1／2／3／5／5… 秒退避，默认总轮询 60 秒、单请求 15 秒，可用 `--timeout/--request-timeout` 改；超时明确报告 `client polling timeout`。POST 传输异常最多三次同 key／body 重试。保存打印的 key（或设置 `OJ_IDEMPOTENCY_KEY`）再恢复失败调用，不能换新 key 猜测重试。

## 一致性备份与隔离恢复

V1 使用停机备份：先停止**所有** Web／Worker 及导题／CLI 写入者（认证 GET 也可能更新 Token 时间），再同时保存完整 SQLite 目录（含 WAL／SHM／journal）、整个 data（测试、题面 assets、头像）、私密配置及对应代码／Judge 镜像版本。不能在两个不同在线 revision 复制 DB 和文件。临时 Job 非必需，未完成任务沿用启动时的安全终结，不自动重执行。

实际维护时按自己的部署停止进程；采用本文部署时为 `systemctl --user stop minioj-worker`、`docker compose stop nginx server`。选择仓库外、私密绝对备份路径，源目录必须对应实际配置／Compose 挂载，而非误备份另一份空库：

```bash
python scripts/backup_restore.py backup --writers-stopped \
  --database-dir ./database --data-dir ./data \
  --destination /absolute/private-backups/snapshot-20261002
```

`--database-name` 默认 oj.db。工具新建 0700 快照目录，检查 SQLite integrity／外键和文件摘要，检测源变化，拒绝 symlink／路径重叠；**不代替你停止或启动服务**。`.env` 和代码／镜像记录另存快照目录之外的私密位置，配置可能有 secret；不要公开备份。成功后才恢复原实例运行。

恢复到**新的隔离目录**，不覆盖正式安装：

```bash
python scripts/backup_restore.py restore \
  --snapshot /absolute/private-backups/snapshot-20261002 \
  --destination /absolute/isolated-restore
```

工具校验 manifest、恢复文件和 SQLite，目标即使是已存在空目录也拒绝。将隔离 Server／Worker 指向恢复 DB/data、独立 Job、唯一 `MINIOJ_WORKER_OWNER`、回环端口及匹配代码／镜像，完成新提交 AC 后，再考虑人工协调正式恢复。失败时保留本次新建的不完整目录供检查，不删除／覆盖原数据；代码／配置／DB／data 应按同版本整体恢复。CF 源码／缓存、Polygon 原包可另外备份，该工具不移除它们。

```bash
python scripts/smoke_test_phase5.py --all-verdicts
```

该脚本仅用自建临时数据，执行 backup → 新目录 restore → 真实 HTTP 客户端根／子路径 → Worker／Docker AC，再验八种 verdict、清理和原快照再次恢复。部署冒烟另经实际隔离 Nginx 调用 Reference Client。开发过程不操作正式库／正式 image tag。

## 数据目录与限制

- SQLite：`database/oj.db`
- Testcase：`data/problems/<problem-id>/tests/`
- 临时 Job：`MINIOJ_JOB_DIR`，未设置时为 `MINIOJ_DATA_DIR/jobs`；每次运行结束后清理自己的目录
- 单个 Testcase 输入或输出文件上限：`MINIOJ_TESTCASE_FILE_LIMIT_BYTES`，默认 16777216 字节
- std／generator 单次运行限制：`MINIOJ_TESTCASE_BUILD_TIME_LIMIT_MS`，默认 10000 ms；`MINIOJ_TESTCASE_BUILD_MEMORY_MB`，默认 512 MiB
- 单个 generator 任务最多用例数：`MINIOJ_GENERATOR_MAX_CASES`，默认 50
- Source 上限：`MINIOJ_SOURCE_LIMIT_BYTES`，默认 262144 字节
- Custom Run 输入上限：`MINIOJ_STDIN_LIMIT_BYTES`，默认 262144 字节
- 编译限制：`MINIOJ_COMPILE_TIME_LIMIT_MS`，默认 30000 ms；`MINIOJ_COMPILE_MEMORY_MB`，默认 512 MiB，题目内存限制更高时取更高值
- stdout + stderr 合并上限：`MINIOJ_OUTPUT_LIMIT_BYTES`，默认 8388608 字节
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
python -m pytest -ra
```

最小 `.github/workflows/ci.yml` 使用 Python 3.12 执行完整检查、安装 Chromium 跑浏览器、检查应用 JS，并仅用 `.env.example` 解析 Compose；不使用正式 Secret，普通 CI 不运行 Docker Judge。真实 Docker integration 由下方独立 smoke 执行。`40ede95` 的 [hosted CI](https://github.com/Sy-SU/MiniOJ/actions/runs/37008639747) 已完整通过 725 项；首次 4 个测试夹具失败和最小修复见 [TODO.md](TODO.md)。更早本地／Docker 证据保留为 [phase5-validation.md](docs/phase5-validation.md) 的历史快照，不代表正式部署。

Docker Daemon 和判题镜像可用时，运行隔离的端到端检查：

```bash
python scripts/smoke_test_stack.py
python scripts/smoke_test_judge.py
python scripts/smoke_test_generator.py
python scripts/smoke_test_sandbox.py
python scripts/smoke_test_worker_failures.py
python scripts/smoke_test_phase5.py --all-verdicts
python -m playwright install chromium
python scripts/smoke_test_phase3_deploy.py
```

这些脚本分别覆盖临时数据库栈流程、全部 Verdict 及退出码／输出边界、真实 generator/std、网络／PID／宿主文件／只读根目录／清理隔离、Testcase 损坏或缺失和镜像不可用时的安全 IE，以及真实 Chromium 经隔离 Compose/Nginx + 宿主 Worker 的 `/minioj/` 流程。部署冒烟使用临时数据库／data、随机回环端口和独立 Compose project，验证登录、网页 Token、Run Sample、Custom Test、Submit／自动更新、AC/CE/TLE、Docker 故障 503 和清理；还会调用旧 `smoke_test_phase4_api.py` 和新 `smoke_test_codeharness_api.py`，两者均不 import MiniOJ，分别经过 Nginx 子路径和 Server 直连根路径，不会部署正式环境。

也可以对已经运行的安装单独执行 HTTP 客户端；它会创建并撤销一个子 Token、执行 Custom Run，并创建一个正式 Submission：

```bash
export OJ_BASE_URL='http://localhost:8000'
export OJ_API_TOKEN='oj_replace_me'
export OJ_PROBLEM_ID='two-sum'
python scripts/smoke_test_phase4_api.py
```

`pytest` 测试套件不需要 Docker，也不会执行不可信程序。`tests/test_problem_pages.py` 的页面浏览器回归使用 Playwright Chromium，缺少 Playwright 或 Chromium 时跳过；执行 `python -m playwright install chromium` 后，可用 `pytest tests/test_problem_pages.py` 验证历史页面无警告、题目链接和其他警告按钮。Docker 冒烟脚本需要正在运行的 Docker Daemon 和 `minioj-cpp20:latest` 镜像；Phase 3 部署冒烟还需要先下载 Playwright Chromium。

## Polygon 题目包导入

admin/system 登录后进入 **管理 → Problems → Import Polygon**，通过文件选择框上传完整 Polygon ZIP，可选指定新的题号和题面语言，然后点击 **Import problem**。自动选择优先中文，其次英文。包须包含 `problem.xml`、HTML 题面及全部已生成输入和答案，允许外层带一个目录。导入不执行 generator、validator 或包内二进制。

保留题名、限制、来源 URL、测试顺序、公开 sample、generated/manual 来源、题面图片和主 C++ 标准解。Checker 源码及本地头文件以带 SHA-256 的快照存入数据库，不经公开／Agent API 暴露。优先使用包内声明的 C++ checker，支持自定义多解判定；标准 checker 缺少源码时，使用仓库内固定版本的 [官方 testlib](https://github.com/MikeMirzayanov/testlib) 实现。包含 19 个非评分标准检查器，例如 `ncmp`、`icmp`、`uncmp`、`lcmp`、`fcmp`、`rcmp4/6/9`、`wcmp` 和 `nyesno`。未知自定义 checker 缺源码、非 C++ checker、评分／交互题、文件 I/O 及仅 TeX/PDF 的题面明确拒绝。重复／软删除题号不覆盖；题目、checker、数据和图片一次提交，失败回滚新增文件。

Checker 按 C++20 编译，所需 UTF-8 `.h/.hpp/.hh/.hxx/.inc` 本地头文件放在源码所在目录／子目录，或在 `files/resources` 声明。优先使用包内 `testlib.h`，没有时补入固定版本。源码包最多 128 个文件、总量 4 MiB；单头文件最多 1 MiB，入口源码沿用 `MINIOJ_SOURCE_LIMIT_BYTES`。不运行上传的二进制。每个提交只编译一次 checker；每例用独立只读沙箱传入输入、选手输出、标准答案三个文件，选手容器不能访问 checker 或答案。Checker CPU／内存不计入选手成绩，默认 Linux testlib 退出码 0 → AC、1/2/4/8 → WA；检查器失败、崩溃、超限、OOM 或评分结果 → 安全 IE，原始诊断仅进内部日志。运行预算独立使用 `MINIOJ_CHECKER_TIME_LIMIT_MS=10000`、`MINIOJ_CHECKER_MEMORY_MB=512`，编译沿用现有预算。

testlib 题的 **Run Sample** 只展示执行结果和参考答案，不用文本比较声称 AC/WA；请用 **Submit** 取得 checker 判定。Custom Run 和共用 HTTP schema 不变。已有 `lines`／`tokens`／`yesno` 题目保持原行为。安装新版后先备份数据库，执行 `minioj init-db` 幂等补齐 checker 列，再按现有流程重建／重启 Server 与 Worker；不会自动覆盖导入题或重判。本轮开发不会自动执行正式升级。

独立 checker 验证不写正式数据库：

```bash
python scripts/smoke_test_checkers.py
python scripts/smoke_test_checkers.py --polygon-package 'store/graph-1-27$linux.zip'
```

默认 ZIP 上限 64 MiB、展开总量 256 MiB、最多 1000 组测试及 10000 个归档条目；单个输入／答案沿用现有 Testcase 上限。只公开题面引用的 PNG/JPEG/GIF/WebP 图片，不公开 hidden、源码及其他包内文件。图片位于 `data/problems/<题号>/assets/`，须与测试数据一起备份。Nginx 导入路由限制为 65 MiB（含 multipart）；提高上传配置时须同步修改。`store/` 运行数据不进入 Git，只有明确 allowlist 的可选 [CF 导题工具](store/cf_import/README.md) 源码／示例／文档可提交；整个 store 不进入 Server Docker 构建上下文。Core 和独立 HTTP Reference Client 不依赖该工具或其模型密钥。

使用本地包补验真实浏览器选文件、Nginx 上传、图片、样例和全部标准解评测：

```bash
python scripts/smoke_test_phase3_deploy.py --polygon-package 'store/graph-1-27$linux.zip'
```

`--server-image IMAGE` 可复用已构建 Server 镜像（只验证运行链路，不视为干净构建通过）；`MINIOJ_DOCKER_IMAGE` 选择冒烟 Worker 镜像。

## 运行时间统计

新评测的 `time_ms` 使用 cgroup v2 记录沙箱进程树 CPU 毫秒，不包含 Docker 启动、调度等待和 CLI 等待，包含少量监督进程 CPU 开销。时间限制按 CPU 消耗执行，另有 `max(1000 ms, 5 × CPU 时间限制)` 的容器内墙钟保护及额外 3000 ms 的 Docker 通信保护。TLE 使用明确超时标记，不再通过 124/137 退出码猜测。

CPU 频率、缓存及共享资源竞争仍可能改变计算开销；历史结果不重新计算。编译阶段沿用宿主墙钟预算，CE 展示编译时间。升级时执行 `docker build -t minioj-cpp20:latest docker/cpp20`，再在准备应用变更时重启 Worker。

执行栈的 soft/hard limit 按题目内存预算设置，避免 Docker 默认 8 MiB 栈导致深递归标准解 RE；容器总内存限制继续生效。

```bash
python scripts/smoke_test_timing.py
```

该脚本覆盖后台负载、子进程 CPU 限制、UID／capability 隔离和报告防篡改；可用 `--baseline-image IMAGE` 比较原宿主墙钟记录。

## 账号输入限制

- 新用户名：3–10 位，仅允许英文字母 `A–Z`、`a–z`；去除首尾空白。公开注册仍禁止使用保留的管理员名称。已有账号仍可使用原用户名登录。
- 邮箱：去除首尾空白并转为小写，总长度最多 254 字符，`@` 前最多 64 字符；接受 ASCII 邮箱和带点的域名，支持 `+` 标签，不接受空白、控制字符、显示名称或带引号的地址。
- 密码：至少 10 字符，UTF-8 编码最多 1024 字节；允许空格、符号和中文，不截断、不去除首尾空白。注册和修改密码必须两次输入一致；登录和校验当前密码也会拦截超长输入。
- 账号相关 POST 请求体最多 16 KiB，超过返回 HTTP 413；无 `Content-Length` 的分块请求同样受限。
- 源码、stdin 和 API testcase JSON 在解析前按配置字段上限及 JSON 编码余量限制总请求体，解析后仍按 UTF-8 字节精确校验。Web std／generator／testcase 的 multipart 和表单请求同时限制流式总大小与单文件大小。
- 浏览器输入限制与服务端校验共同生效；数据库查询使用参数绑定，HTML 模板保留自动转义。
- Settings 生成 API Token 后可点击 **Copy**；若浏览器拒绝剪贴板访问，可手动选择并复制。完整 Token 只展示一次；列表显示实际密钥前 7 位和后 4 位，中间使用 8 个星号，例如 `oj_abcd********wxyz`。数据库只保存 hash 和掩码摘要；旧 Token 无法从 hash 恢复首尾，显示摘要不可用提示。点击 **Revoke** 会立即禁用 Token 并保留 metadata；已撤销 Token 不再显示操作按钮。

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
