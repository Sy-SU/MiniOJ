# Phase 5 implementation and validation — 2026-10-02

## Current release status — MiniOJ V1.0.0 production released

| 发布事实（2026-10-02） | 已完成的实际结果 |
| --- | --- |
| Version | `v1.0.0` |
| Production Release SHA | `65f82f7532ecf8024ad405f7eb1ae877b369914f` |
| Hosted CI | [Checks run 37014381477](https://github.com/Sy-SU/MiniOJ/actions/runs/37014381477)，completed / success；绑定上述 Release SHA |
| 完整回归 | **725 passed（183.35s），0 failed／0 skipped**；Ruff format 117 文件／lint、8 个应用 JS、示例 Compose 均通过 |
| Production migration | passed；连续两次 `minioj init-db` 成功且幂等 |
| Production smoke | passed；范围见下表 |
| Production short observation | passed；短时观察，不是长期稳定性承诺 |
| Annotated Git tag | `v1.0.0` 已创建、推送；本地及远端解析均指向上述 Release SHA |
| Final verdict | **PRODUCTION_READY** |

Phase 5 当前已完成。MiniOJ 冻结的远程求解流程为 **Problem → Submission → Poll → Feedback**，仅通过 HTTP／Bearer Token／JSON；不开发 CodeHarness 的 Agent Loop、LLM Provider、model routing、prompt／agent state、experiment scheduler 或 workspace management。本轮只收尾发布文档：后续文档提交可以推进 `main`，不会改变生产 Release SHA、移动 `v1.0.0` 或重新部署。

### 正式镜像与部署

以下镜像均从最终 committed Release SHA 构建、验收并实际用于正式部署，与下方早期验收镜像区分；没有推送镜像 Registry。

| 镜像 | 正式部署 tag | 本地 image ID／manifest digest |
| --- | --- | --- |
| Server | `minioj-server:v1.0.0-rc1-65f82f7` | `sha256:dbff843851449bf94639bbfe2a25c11557620f415920d67d9f607289c0ef419c` |
| Judge | `minioj-cpp20:v1.0.0-rc1-65f82f7` | `sha256:2dd782f100398c9c3c95fc8af447d534e4a1b05bb3db33b3bcf0b330352ad80c` |

最终 Server 实际安装的 165 个源码／资源文件与 Release SHA 一致，无缺失／多余／不同文件，也没有 `.env`、运行数据或 `.orig`。构建使用未改的标准 Dockerfile／官方基础镜像；最终构建在应用／构建输入一致性核对后复用同轮次依赖层，不是 `FROM` 旧应用镜像。正式 Server／Nginx healthy，宿主 Worker ready 并选择新 Judge；Web 无 Docker CLI／socket。验收通过后默认 `minioj-server:latest`／`minioj-cpp20:latest` 已指向上述镜像，原镜像另存回滚别名。

### 正式备份与迁移

协调停止所有 Web／Worker／导题／CLI 写入方后，一致复制整个数据库目录与全部 data（测试／题面图片／头像／Job）；manifest 共 **8012 文件**，摘要复核、新目录恢复可读性、SQLite integrity 和 foreign keys 均通过。停写后没有 WAL／SHM 残留文件；目录整体备份没有按扩展名遗漏数据库文件。Verified backup 及完整本地 release report 已保留在部署主机，不提交私有 report、凭证或主机绝对路径。

正式数据库连续两次执行 `minioj init-db`，均返回 0，既有 schema 已是当前版本，两次为幂等执行；每次均核对所有既有业务记录完整保留。

| 记录 | 停写后／迁移前 | 两次迁移后 |
| --- | --- | --- |
| Users | 5 | 5 |
| Problems | 179 | 179 |
| Submissions | 391 | 391 |
| Contests | 3 | 3 |

SQLite integrity：**ok**；foreign key violations：**0**。上述是正式停写窗口内的迁移对照，不是实时统计；smoke 仅新增两次正式提交，恢复服务后的正常用户提交与外部 CF 导题增量全部保留，不误作迁移数量异常。

### Production smoke 与 short observation

| 验收项 | 正式环境实际范围／结果 |
| --- | --- |
| Health | `/healthz` HTTP 200 |
| Web／static／Chromium login | 首页、静态资源、登录、题库正常；既有 system／普通账号通过真实密码／CSRF Session 登录；真实 Chromium 无页面脚本错误 |
| API identity | 普通 Bearer 身份、feedback mode 和显式 schema 通过 |
| Problem pagination／sorting | 每页 50、默认／难度双向 null-last、浏览器翻页通过 |
| Old problem-list API compatibility | 无 page 的旧数组及默认创建时间倒序保留 |
| Submission AC | **392：14/14 AC，CPU 19 ms**；实际 Worker claim／新 Judge 路径通过 |
| Submission CE | **393：预期 CE**；未为 smoke 额外创建 WA |
| Feedback | AC／CE HTTP 200，固定核心字段／模式及安全诊断通过 |
| Custom Run | `ok`，OK／2 ms，不创建正式 Submission |
| Contest read-only | 3 场比赛列表、standings／整数 Performance；无比赛修改 |
| Admin read-only | Dashboard、submissions、users／problems navigation；无管理写操作 |
| Hidden-data boundary | 普通身份无 hidden/generated 预览及 std／generator／checker 源码；跨用户提交 404，Secret／Token／内部路径不泄漏 |
| Worker cleanup | 空闲边界无遗留 Worker owner container／Job；共享导题临时目录按各自 owner／活动归属区分 |
| Short observation | 三次观察（21.22s）及部署启动后日志复核：无 5xx／持续 infrastructure error／重复 smoke 提交，队列无异常积压 |

这只是一次低影响生产验收与短时观察，不是长期 SLA、长期稳定性或安全认证。正式环境没有运行 fork bomb、大内存攻击、完整八 verdict／Sandbox stress 或 destructive action；那些较广范围的受控测试证据仍属于下方历史隔离验收。

### Rollback 与 `.orig` hygiene

- Rollback **未触发**。Verified backup、previous Server／Judge images、原配置及从实际旧镜像保留的 Worker 包组成的 rollback point 保留可用；正式 `.env` 字节未变化。旧镜像保留为 `minioj-server:pre-v1-65f82f7`／`minioj-cpp20:pre-v1-65f82f7`。旧运行 Git SHA 无法确认，不猜测；恢复演练只验证备份到新目录，不声称执行了正式回滚。
- `compose.yaml.orig`、`deploy/nginx.conf.orig`、`src/minioj/judge/runner.py.orig` 已取消 Git 跟踪，本地副本／摘要及过去提交的历史均保留。`.gitignore` 沿用 `*.orig`；`.dockerignore` 同时有 `*.orig` 与 `**/*.orig`，真实 BuildKit context 验证覆盖根目录和任意嵌套备份。最终 Server image 不含 `.orig`，没有 history rewrite／amend／force push。

## Historical snapshots — source RC 与发布前交付验收

以下是 historical snapshot，不是 current release status：保留 Phase 5 交付轮次的 **722 项**及当时镜像摘要匹配、提交前审计 **723 passed（624.25s）**，以及源码 RC `40ede95` 的 [hosted Checks CI](https://github.com/Sy-SU/MiniOJ/actions/runs/37008639747) **725 passed（178.42s），0 failed／0 skipped**。该源码 RC 阶段当时为 **MiniOJ V1 source RC complete**，正式上线仍为 READY_WITH_NOTES，尚未操作正式数据库、服务、镜像或 tag；这些边界后来已由顶部正式发布证据关闭。

提交前 RC 审计只补 OpenAPI 既有分页响应头声明、迁移保留回归、忽略规则及文档，并重跑八 verdict／backup→restore HTTP smoke。其旧镜像 166 文件核对为 165 一致、仅 API 元数据不同，是之后 vendor 行尾空白规范化和 CI 测试修复前的快照，不将旧镜像称为后续源码全部字节一致；当时正式 RC image 仍须从 committed SHA 单独构建和验收，后来已完成。以下“本轮／未 commit/push／待验证／未部署”等仅描述当时交付边界。

本轮依据最新附件，仅完成 Phase 5 协议收尾及公开题库分页／排序。在已有脏工作区上继续，HEAD 仍为 `8d8cafce30754ba6d5cb6c41f8e4dc417a901116`。所有已有源码、题库、头像、CF 缓存及 Polygon 数据保留。未 commit/push、部署／重启正式服务、修改正式数据库或替换正式 Docker tag。

## 修改文件（仅本轮，不等同于完整 git diff）

下列文件可能同时包含更早的未提交修改；本轮是增量，不将旧修改归为本轮新功能。

| 文件 | 本轮改动 |
| --- | --- |
| `src/minioj/feedback.py` | 从零 allowlist、模式／角色交集、UTF-8 字节限制与 flags、诊断脱敏、统一 Web／History／API |
| `src/minioj/schemas.py` | Feedback 显式嵌套模型、固定核心字段和状态／verdict／mode 枚举 |
| `src/minioj/server/api.py` | 普通 Submission 策略、Feedback 响应模型、兼容可选分页和 Idempotency-Key |
| `src/minioj/server/errors.py` | 保留原错误包络与 code，新增安全 500／idempotency_conflict，验证错误不回显输入 |
| `src/minioj/server/main.py` | 注册安全未捕获异常处理器 |
| `src/minioj/server/management.py` | History 编译／运行显示复用统一转换 |
| `src/minioj/models.py` | Submission 可空请求哈希及 user/key 唯一索引 |
| `src/minioj/database.py` | 旧库幂等新增请求哈希列／索引，不更改历史记录 |
| `src/minioj/submissions.py` | 同 key 重试／冲突、跨连接写锁、复用已有入队服务 |
| `src/minioj/problems.py` | 公共 SQL 查询、50 题常量、稳定默认／难度排序、字面量搜索 |
| `src/minioj/server/web.py` | SQL count/limit/offset，保留查询／子路径的分页 URL |
| `templates/problems.html` | 无 JS GET 搜索／排序、Previous／Next／页码 |
| `static/style.css` | 题库控制区及手机换行布局 |
| `scripts/smoke_test_codeharness_api.py` | 独立标准库 HTTP Reference Client，分页、同 key 重试、有期限轮询 |
| `scripts/backup_restore.py` | 停写前提的 SQLite＋全部 data 快照、manifest、新目录恢复／拒绝覆盖 |
| `scripts/smoke_test_phase5.py` | 自建备份恢复题库、根／子路径真实 HTTP、全部八种 verdict、清理 |
| `scripts/smoke_test_phase3_deploy.py` | 现有隔离 Nginx 冒烟追加新 Reference Client，不复制部署流程 |
| `.github/workflows/ci.yml` | 最小无 Secret 全仓检查／浏览器 CI，普通任务无 Docker Judge |
| `tests/test_phase5.py` | verdict/mode/role/pending、最终 body sentinel、脱敏／长度、旧异常结果、500／OpenAPI |
| `tests/test_problem_pagination.py` | 所有分页边界、SQL／排序／搜索、真实 HTTP Chromium 根／子路径／手机／无 JS |
| `tests/test_submission_idempotency.py` | 同请求重放／冲突／无 key、跨用户／连接并发／唯一约束、旧库升级 |
| `tests/test_backup_restore.py` | WAL／所有持久数据、拒绝覆盖／symlink／坏快照 |
| `tests/test_codeharness_client.py` | 独立 HTTP、分页、mode、POST 响应丢失、有限轮询不重提、无内部访问 |
| `tests/test_phase1.py`, `tests/test_problem_lifecycle.py` | 旧断言适配固定安全 summary，同时确认内部完整结果保留 |
| `README.md`, `README_zh.md` | 双语契约、客户端／CI／停写备份／隔离恢复操作、升级列说明 |
| `docs/architecture.md`, `docs/codeharness-api.md` | 架构、冻结协议／模式矩阵／单位／错误／幂等／分页与历史边界 |
| `TODO.md`, `docs/phase5-validation.md` | 实现／验收／待验证分开记录，完整文件和证据清单 |

## 最终行为

完整模型见 [HTTP v1 契约](codeharness-api.md)。普通提交仍 `POST /api/v1/submissions` → 202/QUEUED；普通查询保留原全部机器字段，非终态结果为 null。原 `GET /api/v1/agent/submissions/{id}/feedback` 不改路径，授权且存在时所有状态均 200。Feedback 必需字段为 submission_id/status/verdict/feedback_mode/failed_test/compile/execution/diagnostic/summary/summary_truncated，旧可选视图保留允许的字段名。三种 pending／八种 verdict 显式枚举，客户端只读 status/verdict，不解析 summary。

`Idempotency-Key` 可选、按 user 分域，存摘要及数据库唯一索引。相同有效请求回原 ID／202，改变有效请求为 409 `idempotency_conflict`；完成／删题／满队列后仍可重放。旧无头请求及比赛 POST 不改。保留 Phase 4 error.code 名／包络／HTTP 约定；意外内部错误为安全 500，无 traceback、SQL、路径或秘密。

Web `/problems` 每页 50，默认 ID 升序。普通列表 API 不带 page 保留原全量数组与创建时间倒序；显式 page 才 50 题分页＋计数 headers。`difficulty_asc/desc` 两者 null-last，同 rating 按 ID；可选 q 是 ID／title 的字面量子串。所有分页／排序在 SQL 中做，deleted 不计入；无 JS、手机及子路径可用。非法 page／sort 为安全 422，超范围空页／数组，超大整数不触发 SQLite offset 溢出。页码切换保留查询，排序／搜索提交回第 1 页。动态增删不是跨页快照。

| 字段／内容 | full | diagnostic | verdict_only |
| --- | --- | --- | --- |
| 固定身份／status／verdict／mode／安全 summary／已知 failed_test | 有 | 有 | 有 |
| 安全 CE 编译诊断、diagnostic、资源／tests／point metadata | 有 | 有 | null／不暴露 |
| sample failure 预览 | 有 | 无 | 无 |
| hidden/generated failure 预览 | 仅 admin/system | 无 | 无 |
| 内部路径、Docker／traceback／SQL、Secret | 无 | 无 | 无 |

所有内容须同时满足 mode allowlist 与既有所有权／角色权限；full 不提升普通用户权限。未知新增 Judge 字段不自动出现在任何响应，IE 不泄漏 raw failure／compile。编译／运行诊断和 failure 预览最多 1024 UTF-8 字节，不破坏 Unicode；逐字段 truncated flags 保留上游截断。Web `...` 只表示未显示后续数据，不写入 JSON 或裁剪数据库。普通 Submission、Feedback、Web 和 History 统一出口，不靠 CSS／JS 隐藏敏感数据。

## Reference Client 使用

以下命令会在所指定实例创建 Submission。若只做隔离验收，使用下一节的 phase5 smoke。

```bash
export OJ_BASE_URL='http://localhost:8000'  # 或 http://localhost/minioj/
export OJ_API_TOKEN='oj_replace_me'
export OJ_PROBLEM_ID='sum-two'
export OJ_IDEMPOTENCY_KEY='save-a-unique-key-before-first-post'
python scripts/smoke_test_codeharness_api.py --timeout 120 --expect-verdict AC
```

默认源码仅适用于两整数求和示例题；其它题设置 `OJ_SOURCE_CODE`。仅依赖 Python 标准库和 HTTP/Bearer/JSON，不 import MiniOJ，不读 SQLite／数据文件／共享模型，不解析 HTML／summary。先保存 key，POST 传输错误同 key/body 最多三次重试；轮询 1/2/3/5/5… 秒退避，默认总期限 60 秒、每请求 15 秒可配置；报 `client polling timeout` 不会重提。请求拒绝携带凭证的 URL／跟随重定向，不把完整错误体／Token 输出到终端。

## 运行环境与完整回归

| 工具 | 本轮实际版本 |
| --- | --- |
| 日期 | 2026-10-02（Asia/Shanghai） |
| 本地环境 | Conda minioj，Python 3.12.14 |
| pytest / Ruff | 8.4.2 / 0.16.9 |
| Chromium / Node | 153.0.8010.12 / 18.19.1 |
| Docker Client / Server | 29.8.1 / 29.8.1 |
| 干净 Server 容器 | Python 3.12.15、FastAPI 0.142.2、SQLAlchemy 2.1.2、Pydantic 2.13.5、Pillow 12.3.0 |

```bash
conda run --no-capture-output -n minioj python -m pytest -ra --tb=short
```

实际结果：**722 passed in 683.12s（11m23s），0 failed，0 skipped**。所有已有模块／浏览器及新增契约、分页、备份、客户端、并发测试均参与，没有只跑新测试。

增量验证也已实际运行：

```bash
conda run --no-capture-output -n minioj python -m pytest \
  tests/test_phase5.py tests/test_submission_idempotency.py \
  tests/test_phase4.py tests/test_api.py tests/test_phase1.py \
  tests/test_problem_lifecycle.py -ra --tb=short
# 104 passed in 99.07s

conda run --no-capture-output -n minioj python -m pytest \
  tests/test_backup_restore.py tests/test_codeharness_client.py \
  tests/test_problem_pagination.py -ra --tb=short
# 46 passed in 45.15s，包含 8 组真实 HTTP Chromium
```

最终完整静态检查均返回 0：

```bash
conda run --no-capture-output -n minioj python -m ruff check .
conda run --no-capture-output -n minioj python -m ruff format --check .
for source in static/*.js; do node --check "$source" || exit 1; done
gcc -Wall -Wextra -Werror -fsyntax-only docker/cpp20/supervisor.c
docker compose --env-file .env.example config --quiet
systemd-analyze --user verify deploy/minioj-worker.service
git diff --check
```

Ruff **116 files already formatted / All checks passed**；8 个应用 JS；Compose、C、systemd 及 diff 全部通过。CI YAML 以 BaseLoader 解析并核对 push/PR、只读权限与 full pytest step；不把 YAML 解析／本地检查等同于 GitHub runner 执行。

## 真实 Docker／恢复／反向代理

下列命令均已实际运行，环境权限不足的初次尝试与最终复验见下节，不访问正式数据库。

```bash
conda run --no-capture-output -n minioj python scripts/smoke_test_phase5.py --all-verdicts
conda run --no-capture-output -n minioj python scripts/smoke_test_phase3_deploy.py \
  --server-image minioj-phase5-server:20261002-contract
```

- Phase 5 smoke 自建 56 题迫使客户端发现第二页；停止自建写入方／dispose → SQLite 整目录＋data backup → 新目录 restore。分别启动真实根与 `/minioj/` Uvicorn／Worker，独立 Reference Client AC；接着 WA/CE/RE/TLE/MLE/OLE/IE 均以同一 Feedback schema 通过。仅恢复副本注入坏文件得到 IE，备份不变且可再次完整恢复；源、备份、恢复、进程、Job、owner 容器全部清理。
- 隔离 Nginx／Server／宿主 Worker 用上述新干净镜像运行：旧 HTTP 客户端和新 Reference Client 均在 Nginx `/minioj/` 与 Server 直连根路径 AC；真实 Chromium Token、源码 Tab／高亮／复制、Sample／Custom Run／Submit／自动查询、AC/CE/TLE、故障 503、health 和独立 Compose 栈清理通过。本次未传 Polygon 包，不将历史导入实测算作本轮重跑。
- 单独 Judge／Sandbox smoke 在 `/tmp/minioj-phase5-resources-CFB5nS/{jobs,data}`、各自唯一 owner 运行；Worker faults 自带临时 DB/data/jobs，外部 owner 为 `phase5-faults-20261002`。

```bash
MINIOJ_JOB_DIR=/tmp/minioj-phase5-resources-CFB5nS/jobs \
MINIOJ_DATA_DIR=/tmp/minioj-phase5-resources-CFB5nS/data \
conda run --no-capture-output -n minioj python scripts/smoke_test_judge.py

MINIOJ_JOB_DIR=/tmp/minioj-phase5-resources-CFB5nS/jobs \
MINIOJ_DATA_DIR=/tmp/minioj-phase5-resources-CFB5nS/data \
conda run --no-capture-output -n minioj python scripts/smoke_test_sandbox.py

MINIOJ_WORKER_OWNER=phase5-faults-20261002 \
conda run --no-capture-output -n minioj python scripts/smoke_test_worker_failures.py
```

上述已清理的临时路径只记录实际命令，不是可复用正式目录；重新执行须 `mktemp -d` 创建新的私有路径并先建立 jobs/data。实际七种正常 verdict、主动 exit 124/137 → RE、有限／无限输出 → OLE、编译输出 CE／截断、网络拒绝／PID／宿主文件／只读根及 owner／Job 清理通过。坏／缺 testcase、不可用镜像 → FINISHED/IE，公开说明无内部诊断且 health 200。

## 干净构建、失败修复和清理

- 使用仓库标准 Dockerfile 和 **--no-cache**，独立临时 BuildKit 从官方基础镜像完整下载／安装依赖，没有叠加旧 MiniOJ 应用层。基础镜像 `python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016`。
- 验收 tag **minioj-phase5-server:20261002-contract**；Docker image inspect ID **sha256:04b00e04c21f0b9839a40b1b609d8239e348f39d9068d6414970c7cc59733771**。Judge 镜像仍为 **sha256:1e126bd3685cd55f15c6027c2bd64419b7c822a37132ab279475847c11aedd41**。
- 只读、无网络、无正式数据挂载的镜像检查：Feedback schema／哈希列／题库模板存在，没有 `.env`、数据库、用户 testcase／头像或代理环境；实际安装的 11 个 Python 模块＋题库模板／样式共 13 个 SHA-256 与当前代码一致。构建后修改的是说明文档，不将镜像内早先 README 打包文本称为最后文档字节完全一致。
- 首次新增测试中 SQL 断言混淆 count／行加载、Playwright page API 参数错误，修正测试后复验；MLE smoke 测试源码修正 volatile 对象后获得真实 MLE，没有调整 Judge 判据。Standalone 资源 smoke 的临时 Job 目录补初始化后通过。systemd 校验首次受沙箱 socket 权限限制，随后在允许环境单独返回 0；最终矩阵以通过的复验为准。
- 未修共享 daemon 既有失效代理。只通过独立构建器／私有 socket／临时 buildx 配置和已有宿主代理完成构建，无正式 Docker socket／DB/data 挂载。只读公共 CA；未把代理或秘密写入镜像。
- 独立 `minioj-phase5-builder-20261002` 注册、`minioj-phase5-buildkit-20261002` 容器及其匿名缓存已删除，`/tmp/minioj-phase5-buildx-ayHgmv` 与资源临时目录已清理；缓存可重新构建，不删除任何用户数据。最终 Docker 只读检查正式 Server／Nginx 健康、共享构建器仍在、无本轮故障 owner 残留；保留新验收镜像供用户选择。

## 该交付轮次的未完成／下一步（历史记录）

下列是当时未完成事项，不改写该轮证据；其中 hosted CI、正式备份／迁移／部署已在当前发布记录中完成。原缺失截图包与 V1 范围外功能仍维持原边界。

- **待验证：** GitHub 托管 CI 首次运行。本轮未 commit/push，因此没有远端 runner 证据。
- **未执行：** 正式协调停机／一致性备份／init-db 兼容升级／部署；需另外授权，不能用本轮隔离测试代替正式升级。
- 原 V1 范围外交互／评分题、冻结榜／私密／虚拟比赛、永久 rating、多 Worker 等不属于本轮；缺失 `dyeyourname` 截图包保持旧待验证状态。旧 `.orig` 和数据未删除。
- 建议下一步先审查整份脏工作区的归属与提交范围，经确认后 commit/push，观察首次 CI；另行安排正式升级窗口。

当时仅针对 Phase 5 的建议 message：`feat: freeze Phase 5 HTTP contract and add paginated problem discovery`。RC 整体审计后的累积 Core／独立 CF 两组提交方案见 TODO 顶部，不能按此历史建议遗漏之前的未提交实现。

## git status --short（Phase 5 交付时历史快照，非当前状态）

以下包含该轮以及之前全部未提交修改，并非只有 Phase 5；`D templates/problem_deleted.html` 和用户 data/store 是原工作区状态，不是本轮删除／新增用户数据。未 staging／commit，HEAD 不变。这里曾列出的运行时题库 PNG 已在后续 RC 审计中补忽略、保留磁盘文件，不属于当前可提交范围；当前状态见最新审计报告。

```text
 M .dockerignore
 M .env.example
 M .gitignore
 M README.md
 M README_zh.md
 M TODO.md
 M compose.yaml
 M deploy/nginx.conf
 M docker/cpp20/Dockerfile
 M docs/architecture.md
 M pyproject.toml
 M scripts/smoke_test_judge.py
 M scripts/smoke_test_phase3_deploy.py
 M src/minioj/accounts.py
 M src/minioj/cli.py
 M src/minioj/config.py
 M src/minioj/database.py
 M src/minioj/feedback.py
 M src/minioj/judge/checker.py
 M src/minioj/judge/runner.py
 M src/minioj/models.py
 M src/minioj/problems.py
 M src/minioj/rendering.py
 M src/minioj/schemas.py
 M src/minioj/server/api.py
 M src/minioj/server/dependencies.py
 M src/minioj/server/main.py
 M src/minioj/server/middleware.py
 M src/minioj/server/uploads.py
 M src/minioj/server/web.py
 M src/minioj/worker/main.py
 M static/problem.js
 M static/problem_form.js
 M static/style.css
 M static/submission.js
 M templates/admin.html
 M templates/base.html
 D templates/problem_deleted.html
 M templates/problem_detail.html
 M templates/problem_form.html
 M templates/problems.html
 M templates/settings.html
 M templates/submission_detail.html
 M templates/submissions.html
 M tests/test_accounts.py
 M tests/test_api.py
 M tests/test_config.py
 M tests/test_database_upgrade.py
 M tests/test_docker_runner.py
 M tests/test_judge.py
 M tests/test_phase1.py
 M tests/test_problem_lifecycle.py
 M tests/test_testcase_builds.py
 M tests/test_tokens.py
 M tests/test_worker.py
?? .github/
?? data/problems/T1003/
?? docker/cpp20/supervisor.c
?? docs/codeharness-api.md
?? docs/contest.md
?? docs/permissions.md
?? docs/phase5-validation.md
?? docs/rejudge.md
?? scripts/backup_restore.py
?? scripts/cf_import_fixture.py
?? scripts/smoke_test_cf_import.py
?? scripts/smoke_test_checkers.py
?? scripts/smoke_test_codeharness_api.py
?? scripts/smoke_test_management.py
?? scripts/smoke_test_phase4_api.py
?? scripts/smoke_test_phase5.py
?? scripts/smoke_test_timing.py
?? src/minioj/avatars.py
?? src/minioj/contest_performance.py
?? src/minioj/contests.py
?? src/minioj/judge/testlib.py
?? src/minioj/permissions.py
?? src/minioj/polygon.py
?? src/minioj/profiles.py
?? src/minioj/server/contests.py
?? src/minioj/server/errors.py
?? src/minioj/server/management.py
?? src/minioj/server/profiles.py
?? src/minioj/submissions.py
?? src/minioj/vendor/
?? static/code.js
?? static/contest_form.js
?? static/default-avatar.svg
?? store/
?? templates/contest_detail.html
?? templates/contest_form.html
?? templates/contest_standings.html
?? templates/contests.html
?? templates/judge_history.html
?? templates/management_base.html
?? templates/management_contests.html
?? templates/management_dashboard.html
?? templates/management_problems.html
?? templates/management_submissions.html
?? templates/management_system.html
?? templates/management_users.html
?? templates/polygon_import.html
?? templates/problem_not_found.html
?? templates/profile.html
?? templates/submission_table.html
?? tests/fixtures/
?? tests/test_backup_restore.py
?? tests/test_cf_import.py
?? tests/test_code_ui.py
?? tests/test_codeharness_client.py
?? tests/test_contest_performance.py
?? tests/test_management.py
?? tests/test_management_browser.py
?? tests/test_management_migration.py
?? tests/test_phase4.py
?? tests/test_phase5.py
?? tests/test_polygon.py
?? tests/test_problem_pages.py
?? tests/test_problem_pagination.py
?? tests/test_submission_idempotency.py
?? tests/test_submission_results.py
?? tests/test_testlib.py
```
