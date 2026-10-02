# MiniOJ 实施与验收 TODO

更新日期：2026-10-02。依据：独立 OJ 规划、新功能附件、网页体验、Contest 增量、Phase 5＋题库分页及最新 V1 Release Candidate 审计附件。架构和协议见 [docs/architecture.md](docs/architecture.md)、[冻结契约](docs/codeharness-api.md)。

Phase 0–1 已提交并推送为 `dbeb9e6`，Phase 2–3 已按要求提交并推送为 `8d8cafc`。经用户授权，完整累积实现已分为 Core `a02bd86` 与 CF 工具 `18f94c3` 两个提交并正常推送 `origin/main`；首次 hosted CI 发现的测试夹具问题由独立 fix `40ede95` 修复。该提交的完整 hosted CI **725 passed，0 failed／0 skipped**，Ruff／JS／示例 Compose 全绿，当前为 **MiniOJ V1 source RC complete**；正式上线仍为 READY_WITH_NOTES。未部署／迁移正式环境、替换正式镜像或创建 tag；全部用户数据保留。下方“未 commit/push”等均为各轮历史边界，以顶部最新证据为准。

## V1 源码 RC 提交与 hosted CI（2026-10-02；源码验收完成）

- [x] 已提交／推送：`a02bd86b74e116b8d157dbcc43bb083c4fbfa0ce` — `feat: prepare MiniOJ V1 release candidate`，139 文件、20,479 insertions／636 deletions；`18f94c31c10d05cbc48e298ff8762066beb12d12` — `feat: add isolated Codeforces import tooling and reference selection`，26 文件、4,155 insertions／0 deletions。逐文件 allowlist staging、staged stat／name-status／check 通过，不强拆共享文件，不 force／amend／改写历史。
- [x] 推送实测：初始远端／工作区基线 `8d8cafce30754ba6d5cb6c41f8e4dc417a901116`；首次 push 前 HEAD 与 push 后 `origin/main` 均为 `18f94c31c10d05cbc48e298ff8762066beb12d12`，推送前后 `git status --short` 为空。
- [x] 安全复核：初始 268 文件与发布审计 SHA-256 全部一致；两个新提交未加入 `.env`、DB/WAL/SHM、runtime 题库／头像／Job、CF 密钥／cache、个人题解、generated evidence 或 ZIP。3 个已知私密值和常见凭证模式均无命中；题库 PNG、生成审计 JSON、其余用户数据保留。
- [x] 首次 hosted CI 终态：[Checks run 37007186704](https://github.com/Sy-SU/MiniOJ/actions/runs/37007186704)，commit `18f94c3`，**failure：4 failed／719 passed（182.08s）**。Ubuntu 24.04、Python 3.12.14、pytest 8.4.2、Ruff 0.16.10、Playwright 1.63.0／Chromium 153.0.8010.12；依赖／Chromium 安装和 Ruff format／lint 通过，pytest 失败后 JS／Compose 被跳过，不称为全部 CI 通过。
- [x] 原因／最小修复：四个 legacy `/admin` 浏览器组合把 TestClient 的 303 原样交给 Chromium，随后对虚拟 `testserver` 的重定向产生 DNS 依赖，报 `ERR_NAME_NOT_RESOLVED`。仅修改 `test_problem_pages.py` 的请求拦截夹具，在 TestClient 内跟随重定向；新增根／子路径 303＋Location＋目标 Dashboard 断言，不修改应用、HTTP、DNS／proxy、依赖或跳过测试。共享夹具相关 `test_problem_pages.py`／`test_submission_results.py` **70 passed（62.00s）**，Ruff／format／diff check 通过。
- [x] 已提交／推送独立修复：`40ede9585a057760d463d154c8fea186ef195fee` — `fix: resolve browser fixture redirects inside isolated client`，2 文件、29 insertions／7 deletions；push 前 HEAD／push 后远端 main 为该 SHA，工作区干净，未 amend 之前两个提交。
- [x] Hosted CI 验收通过：[Checks run 37008639747](https://github.com/Sy-SU/MiniOJ/actions/runs/37008639747)，绑定 `40ede9585a057760d463d154c8fea186ef195fee`，push／attempt 1，**completed / success**，2026-10-02 12:49:34 UTC 完成。完整 `python -m pytest -ra` **725 passed（178.42s），0 failed／0 skipped**；包含原 723＋新增 2。Ruff format **117 文件**／lint、8 个应用 JS、示例 Compose 均实际执行通过；不是仅凭 workflow 文件或本地检查勾选。
- [x] 当前状态：**MiniOJ V1 source RC complete**，不是 production deployed。源码／测试以已验证的 `40ede95` 为基准，后续仅同步发布文档，不宣称旧验收镜像与当前字节全部一致。runner 另有 action Node 20 弃用警告，但实际强制 Node 24 且步骤成功，本次不为非阻塞警告扩大 workflow 改动。
- [ ] 遗留跟踪边界：`compose.yaml.orig`、`deploy/nginx.conf.orig`、`src/minioj/judge/runner.py.orig` 从旧远端基线就已跟踪，两个新提交没有增删或改写它们，字节／SHA-256 与旧历史和磁盘完全相同。之前仅检查 ignored `.orig` 样本不能证明所有旧备份均已取消跟踪；已询问是否只取消 Git 跟踪并保留磁盘，不擅自删除或改写旧历史。

后续需从 committed SHA 构建 RC image、协调停止全部写入方并做一致性备份、migration、deployment、production smoke／验证／回滚预案；完成正式部署验证后才考虑最终 `v1.0.0`。可以建议 `v1.0.0-rc1`，本轮不自动创建任何 tag，也不执行这些正式操作。下方发布审计及更早阶段保留为提交前历史快照。

## V1 Release Candidate 发布审计（2026-10-02；提交前快照）

**结论：READY_WITH_NOTES。** 可按下列范围准备提交；不是上线批准。GitHub 托管 CI 首跑、正式停机升级／部署仍未执行；旧验收镜像与本轮 OpenAPI 元数据存在一处明确差异。本轮只审计／修补确定缺口，不开发新功能，不改共用 HTTP 行为，不自动提交／推送。

### 整个工作区的归属与发布安全

HEAD／branch：`8d8cafce30754ba6d5cb6c41f8e4dc417a901116`／`main`；index 无 staged changes。审计开始为 54 modified、1 deleted、67 个折叠 untracked 条目（展开 111 文件）；补齐运行时图片忽略后为 **54 modified、1 deleted、66 个折叠 untracked 条目（展开 110 文件）**。共 165 个可提交文件操作：Core 累积 139、CF 工具与测试 26。删除的旧模板是已有修改，由 `problem_not_found.html` 取代，并非本轮删除数据。

| 分类 | 文件／内容边界 | 建议归属 |
| --- | --- | --- |
| A：Core／Phase 5 | feedback／schemas／API／errors、提交幂等／分页／数据库、独立标准库客户端、备份恢复、CI、对应测试与文档 | Core 提交，与 B 的共享文件一起保留 |
| B：此前累积增量 | accounts／permissions、管理／profiles／avatars／Contest／Performance／JudgeRun、CPU supervisor、Polygon／testlib／上游许可证、源码／逐点 UI、templates／static、部署配置及回归 | Core 提交；不强拆同一 models/API/template 文件或重写历史 |
| C：CF 工具源码 | `store/__init__.py`、allowlist 的 `store/cf_import/*.py`／README／config.example、`tests/test_cf_import.py`、5 个合成 fixture、`scripts/cf_import_fixture.py`／`smoke_test_cf_import.py` | 独立第二提交，共 26 文件；Core 中已有管理 API／checker 支持留在第一提交 |
| C：CF 私有产物与历史修复 | config／secrets／state／AC cache、个人 2092E／2107C 源码、生成 checker／tests／LLM response／logs、参考来源审计 JSON | 仅本地保留，不进上述任一提交；旧两题答案核验不是本轮再次重放证据 |
| D：运行时／本地资料 | `.env`、database（SQLite／WAL／SHM／备份）、题库及 assets、avatars、jobs、ZIP、旧 `.orig`、缓存／截图／临时构建资料 | 不提交、不删除；实际部署／数据文件未操作 |

- [x] 已有实现／修复：`.gitignore` 改为忽略整个运行时 database／data/problems，保留 `.gitkeep`；`.dockerignore` 同样覆盖整个数据库目录，防止其他 SQLite 文件名或备份进入构建 context。此前 untracked 的 T1003 PNG 已忽略，**原 8433 字节文件仍在**。
- [x] 验收通过：扫描全部 268 个现存 tracked＋可提交 untracked 文件的字节、大小／类型／SHA-256；3 个已知本地私密值仅在内存中与候选文件比对，不输出原文。没有已知值命中、常见 Token／provider／private-key 模式命中、运行数据候选、symlink 或超过 1 MiB 候选。60 个二进制候选均为既有 KaTeX 字体；testlib 含固定上游来源和 MIT 许可证。该检查不是历史 Git 凭证清理或渗透测试认证。
- [x] 验收通过：真实 `.env`／DB/WAL/SHM、非 `.db` 示例、头像／Job／题库 PNG、CF 本地配置／密钥／账号 cache、`store/codeforces/2092/E.cpp`／`2107/C.cpp`、`store/generated/reference-priority-audit-20261002.json`（72999 字节）和 `.orig` 的 check-ignore 通过；文件不移除。ignored 内容的元数据／目录归类确认 store generated、个人源码／ZIP 不在候选中，未把秘密或完整审计产物写入发布文档。
- [x] 经用户批准：**仅**公开 `.env.example` 将 HTTP bind 改为 `127.0.0.1`；实际 `.env`／Compose 旧 fallback 未改。README 双语改为 loopback 示例并说明远程绑定须显式配置；遗留 fallback 的本机地址是部署可移植性备注，不是凭证，也没有擅自改变网络暴露策略。

### 契约、迁移与文档缺口

- [x] 已有实现／修复：`GET /api/v1/problems` 补四个原本已返回的分页响应头 OpenAPI 声明及回归，不改变 runtime。无 page 仍原数组／默认创建时间倒序，显式 page 每页 50；双向 null-last／ID 次序、SQL limit/offset/count、非法／越界／删除／字面量搜索沿用原规则。
- [x] 验收通过：普通 Submission 原固定字段／可空类型、3 pending／8 verdict 的 Feedback 200／模式角色交集／最终 body sentinel、Idempotency 同 user/key/body 原 ID／不同 body 409／跨用户／6 连接并发及唯一索引由全量和针对性回归锁定；未改路径、字段、202／200／错误包络或比赛 POST。
- [x] 已有实现／补回归：临时 pre-Phase-5 SQLite 保留 11 张已有业务表的逐行快照，包含 User／Problem／TestCase／Sample／Token／Contest／关联／参加者／Submission／两次 JudgeRun／迁移标记。只在测试副本去除请求哈希列／索引，连续两次 init-db 后仅增加 nullable 请求摘要及唯一索引；原整数 id、角色、密码 hash、Token hash／撤销 metadata、微秒时间、源码和当前／历史成绩完全保留；四个测试／题面图／头像文件字节不变，integrity／foreign_key_check 通过。旧角色、preview／checker／testcase 列和旧 ID 升级回归也通过。**未升级正式数据库。**
- [x] 已有实现／修复：澄清 pending 的 started_at 在领取后可有值、finished_at 完成前为 null，不能宣称全部时间为 null；修正 CF README 的“无需迁移”“API 无幂等键”、store 全目录不进 Git及 Nginx 所有其他路径为 2 MiB 等过时文案。CF 工具尚不发送 Idempotency-Key 的缺口明确保留，本轮不新增客户端能力。Core／CodeHarness 边界与可选 CF 工具职责分开。
- [x] 文档同步：本 TODO、README 双语、architecture、冻结契约、phase5-validation 和 CF 工具 README 的当前／历史／待验证状态一致；此前 722 项和 13 文件镜像匹配均明确为历史快照。

### 本轮实际验收

环境：Conda `minioj`；Python **3.12.14**、pytest **8.4.2**、Ruff **0.16.9**、FastAPI **0.142.1**、SQLAlchemy **2.1.1**、Pydantic **2.13.5**、Pillow **12.3.0**、Playwright **1.63.0**、Chromium **153.0.8010.12**、Node **18.19.1**、Docker Client／Server **29.8.1**。

```bash
conda run --no-capture-output -n minioj python -m pytest -ra --tb=short
# 723 passed in 624.25s，0 failed／0 skipped；包含全部此前模块、CF 和真实 HTTP 浏览器
conda run --no-capture-output -n minioj python -m pytest \
  tests/test_management_migration.py tests/test_database_upgrade.py \
  tests/test_submission_idempotency.py tests/test_phase5.py \
  tests/test_problem_pagination.py -ra --tb=short
# 86 passed in 75.29s；初次 TestCase 导入命名产生 collection warning，改为别名后全量无 warning
conda run --no-capture-output -n minioj python -m ruff check .
conda run --no-capture-output -n minioj python -m ruff format --check .
# All checks passed；117 files already formatted
for source in static/*.js; do node --check "$source" || exit 1; done
gcc -Wall -Wextra -Werror -fsyntax-only docker/cpp20/supervisor.c
docker compose --env-file .env.example config --quiet
docker compose config --quiet
systemd-analyze --user verify deploy/minioj-worker.service
git diff --check
conda run --no-capture-output -n minioj python scripts/smoke_test_phase5.py --all-verdicts
```

- [x] 验收通过：所有静态命令返回 0；CI YAML 本地 BaseLoader 解析并核对 push/PR、contents:read、full pytest／浏览器／lint／示例 Compose、无 Secret／Docker Judge step。**没有 GitHub 托管 runner 执行证据。**
- [x] 提交前补验：首次 `git diff --cached --check` 发现此前未跟踪的 `vendor/testlib/testlib.h` 有 38 行上游行尾空白；普通工作区差异检查未覆盖这些新文件。仅移除行尾空白并在 `UPSTREAM.md` 声明规范化，许可证和源代码 token 不变；修复前后 GCC 预处理内容（仅忽略行尾空白）SHA-256 一致，`wcmp.cpp` C++20 语法检查通过，`python -m pytest tests/test_testlib.py tests/test_polygon.py -ra --tb=short` **86 passed（52.19s）**。上面的 723 项是此次空白规范化前的全量快照，最终提交仍须等待 hosted CI；不将旧镜像扩大为规范化后的字节一致验收。
- [x] 验收通过：当前源码重新自建 56 题，backup → restore → HTTP Reference Client 根／`/minioj` AC → 真实 Worker／Docker WA/CE/RE/TLE/MLE/OLE/IE，原快照再次恢复通过。仅破坏恢复副本注入 IE；测试进程、owner 容器、Job／临时目录清理，未读写正式库。未重复上轮 Nginx／Polygon 包或 standalone sandbox 冒烟，不冒充本轮重跑。
- [x] 验收通过：已有 `minioj-phase5-server:20261002-contract` 镜像 ID 仍为 `sha256:04b00e04c21f0b9839a40b1b609d8239e348f39d9068d6414970c7cc59733771`。无网络／只读／无宿主挂载临时容器，对实际安装源码、模板、静态／vendor 及 docker 资源共 **166 文件**重新比对：**165 一致，仅 `src/minioj/server/api.py` 的本轮 OpenAPI 元数据不同**，无缺失／多余文件，无 `.env`、运行数据／proxy env。这是复用镜像检查，不是当前源码的新干净构建或本轮 Nginx 部署验收；本轮未重建镜像、替换 tag、修改共享代理或部署服务。

### 建议提交方案与剩余事项（仅建议，未执行）

推荐 **A：Core 与 CF 工具分开提交**，不强行拆分共用文件，也不重建模块／改写历史。

1. `feat: prepare MiniOJ V1 core release candidate`：139 个 Core 文件操作。范围为当前变更的根配置／双语 README／TODO／pyproject、`compose.yaml`／`deploy/nginx.conf`、docker、docs、src/minioj、static、templates、CI；scripts 包括本次客户端／备份／Phase 4–5／management／checker／timing／既有 judge 和 deployment smoke；tests 包括全部变更和新增应用测试，但**排除**第二组的 CF test／fixture 和 scripts。保留 vendor 许可证与已删除旧模板。共享管理 API／checker 支持与 Core 归组。
2. `feat: add isolated Codeforces import tooling and reference selection`：26 个文件，仅为上表 C 工具源码、示例／README、`tests/test_cf_import.py`、`tests/fixtures/cf_import/*` 的五个合成文件及两个 CF smoke／fixture 脚本。先有 Core 依赖再提交工具，最终整体检查完整 release；不承诺每个中间 commit 都已单独运行 CI。

避免直接对整个 store/data/database 或未知文件 `git add -f`；两个 commit 的明确文件清单／staged diff 仍需用户授权后实际复核。Core 内的 Phase 5／Contest／UI 已混合在同一文件，不推荐为“阶段整洁”强拆 hunk。原 2092E／2107C 的算法源码与历史答案修复仍是本地数据，不加入任何源代码提交。

- [ ] 下一步经用户确认上述范围后，才 staging／commit／push，再观察 GitHub CI 首跑；本轮不执行。
- [ ] 正式停写／一致性备份／init-db／新版镜像构建／部署和上线验收需独立授权；旧镜像的 metadata 差异在实际打包时自然随当前源码进入新镜像，不将它提前冒充通过。
- [ ] CF 导入器的可选幂等头采用、缺失 Polygon 截图包、未来赛制／多 Worker 等保持原边界，不是 Core RC 提交阻塞；默认共享 Docker daemon 的旧代理和 Compose fallback 未修改。所有本地数据／旧 `.orig` 保留。

## Phase 5 交付验收快照（2026-10-02；RC 审计前）

## Phase 5 本轮交付与验收（2026-10-02）

范围为最新附件指定的 HTTP 协议收尾和公开题库分页／排序；不开发 CodeHarness 内部，不增加赛制、永久 rating、多 Worker 或正式部署。实现清单与验收分开如下；命令、版本、镜像及交付边界详见 [本轮验收记录](docs/phase5-validation.md)。

| 范围 | 已有实现 | 本轮验收 |
| --- | --- | --- |
| HTTP 契约 | [x] Feedback 显式模型／固定核心字段、三种 pending 和八种 verdict；保留原路径、普通 Submission 字段、202／200／错误包络 | [x] OpenAPI／最终响应回归；八种 verdict 真实 Docker HTTP 链路 |
| Feedback Policy | [x] 从零构造 allowlist ∩ role，三模式统一作用于普通 API／Feedback／Web／History；UTF-8 1024 字节、truncated flags、诊断脱敏 | [x] 8 verdict × 3 mode × 5 身份，pending、异常旧结果、sample 可用和 hidden/generated 最终 body sentinel |
| 提交重试 | [x] 可选 Idempotency-Key、哈希及数据库唯一索引／写锁；旧无头行为不变，同请求原 ID，冲突 409 | [x] 完成／删题／满队列后的重放、六连接并发、跨用户、旧库两次升级、丢失 POST 响应 |
| 题库页面 | [x] SQL 分页每页 50、默认 ID 升序、难度双向 null-last＋ID 稳定次序、字面量搜索、无 JS 查询保留 | [x] 0/1/49/50/51/100/101 题、非法／超大 page、排序／删除／搜索；根／子路径 × 桌面／手机 × JS 开关 8 组真实 HTTP Chromium |
| 普通列表兼容 | [x] API 不带 page 保留原数组／默认创建时间倒序；带 page 才分页并添加计数 headers | [x] 旧调用、响应模型、SQL count/limit/offset 与全部分页参数回归 |
| 独立 Reference Client | [x] 仅标准库 HTTP/Bearer/JSON，Me → 分页 → 清洗题目 → 提交 → 有限轮询 → Feedback；不读内部／DB／文件／summary | [x] 根与 `/minioj/` 实际 HTTP、独立 Nginx；超时不重提、POST 同 key 重试和模式一致性 |
| 备份／恢复 | [x] 停止全部写入方后复制 SQLite 整目录含 WAL/SHM、全部 data 含题库／图片／头像，manifest；只恢复新目录 | [x] 哈希／WAL／损坏／symlink／拒绝覆盖；独立恢复后根与子路径 AC，再次恢复证明备份未被判题改写 |
| 文档／CI | [x] 双语 README、架构、冻结契约、操作／维护和最小 GitHub Actions，无真实 Secret，普通 CI 不跑 Docker | [x] 全部本地同等检查、CI YAML 解析；[ ] GitHub 托管 runner 首次执行（未 push，不能外推） |

**本轮实际证据：** Conda `minioj`，Python **3.12.14**、pytest **8.4.2**、Ruff **0.16.9**、Chromium **153.0.8010.12**、Node **18.19.1**、Docker Client／Server **29.8.1**。

- `conda run --no-capture-output -n minioj python -m pytest -ra --tb=short`：**722 passed（683.12s），0 failed／0 skipped**，包含既有全部测试，而非仅新增用例。
- 全仓 Ruff lint／format **116 文件**、8 个应用 JS `node --check`、supervisor C `-Wall -Wextra -Werror -fsyntax-only`、示例 Compose 静态解析、systemd unit 和 `git diff --check` 通过。CI YAML 已本地解析，远端未执行。
- `python scripts/smoke_test_phase5.py --all-verdicts`：自建 56 题、离线 backup → restore → 独立客户端根／子路径 AC，以及 WA/CE/RE/TLE/MLE/OLE/IE 全部通过；只破坏恢复副本注入 IE，备份可再次完整恢复。
- 标准 Dockerfile 从官方 `python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016` **无应用缓存从零构建**；保留独立验收镜像 `minioj-phase5-server:20261002-contract`，镜像 ID `sha256:04b00e04c21f0b9839a40b1b609d8239e348f39d9068d6414970c7cc59733771`。容器 Python 3.12.15、FastAPI 0.142.2、SQLAlchemy 2.1.2、Pydantic 2.13.5、Pillow 12.3.0；无网络／只读检查未打包 `.env`、数据库、用户题库／头像或代理环境，实际安装模块／模板／样式共 13 个摘要与当前源码一致。
- `python scripts/smoke_test_phase3_deploy.py --server-image minioj-phase5-server:20261002-contract`：该新镜像的独立 Nginx／Server／宿主 Worker；旧机器客户端和新 Reference Client 均通过根与 `/minioj/` AC；真实 Chromium Token／Tab／源码复制／Sample／Custom Run／AC/CE/TLE、故障 503／health／清理通过。本次没有重新指定 Polygon 包，不将其历史实测扩大为本轮包验收。
- 当前 Judge 镜像 `sha256:1e126bd3685cd55f15c6027c2bd64419b7c822a37132ab279475847c11aedd41` 的 Judge／Sandbox／Worker fault 冒烟通过：七种正常 verdict、主动退出 124/137、有限／无限输出和编译截断；网络／PID／宿主文件／只读根；损坏／缺失 testcase 与不可用镜像均安全 IE，health 200，无独立 owner 容器／Job 残留。

**修复／复验说明：** 首次新增测试发现测试 SQL 断言把 count 当作行加载、Playwright page 构造参数错误，均修正后复验；首次 MLE smoke 的 volatile 修饰对象不正确，修正测试源码后真实 MLE 通过，没有放宽 Judge。单独资源 smoke 最初遗漏临时 Job 目录初始化，补建私有目录后通过。systemd 校验首次受沙箱权限限制，随后在允许的环境单独执行，返回 0。既有共用 HTTP 约定未改名，存储的完整 Judge 结果未裁剪／重写。

**剩余／下一步：** GitHub 托管 CI 待经用户授权提交／推送后首跑；正式升级、协调停机备份／迁移和上线均未执行，需单独授权。旧 `.orig`／本地数据不删除；截图缺失的 `dyeyourname` 包及原 V1 范围外项目继续保留原状态，不是 Phase 5 阻塞。默认共享 Docker daemon 代理未修复／修改；独立临时构建器、socket、缓存和测试目录按本轮 owner 清理，正式服务和镜像 tag 保持不变。

## 导题参考程序优先级与历史答案修复（2026-10-02）

- [x] 已有实现：[导题工具](store/cf_import/README.md) 默认无后缀解 → `__Fast` → `__Good`，后缀大小写一致；`__Good` 作为对拍辅助候选，其名称不表示 AC。保留显式 `[references]` 覆盖，选择规则版本、候选顺序／源码摘要进入缓存，防止旧 `verified` 数据继续沿用旧选择。示例配置同步。
- [x] 验收通过：审计 129 道已导入题目的参考来源，仅 2092E、2107C 用过 `__Good`。两题全部现有数据在隔离 Docker 重放并独立核验：2107C 的 `__Good` 有 6 处错答案，正式解通过 21,694 组；2092E 的已有答案正确，但正式解的指数模 `mod` 写法错误，已最小修正并通过 20,047 组。前述数字不代表其它 127 题的算法或答案独立验收。
- [x] 验收通过：通过管理员 API 按正式解重新导入两题，2107C 的两份输出文件修正，全部输入摘要保留；2092E 全部输入／输出摘要保留，生成器／validator／checker 沿用原产物。真实 Worker 验证提交 293 **27/27 AC**、提交 298 **20/20 AC**；原误判提交 291 保留评测历史并重判为 **27/27 AC**。
- [x] 验收通过：`tests/test_cf_import.py` **37 passed**，Ruff lint／format 与差异检查通过；本地完整证据为 `store/generated/reference-priority-audit-20261002.json`，原产物和状态备份为 `/tmp/minioj-reference-priority-20261002/`。本轮改动未提交／推送；导题规则按本节和工具 README 继续执行。

## 比赛表单保存换行修复（2026-10-02）

- [x] 已有实现：比赛题目 ID 按真实换行回显，保存后不再变成字面量反斜杠加 n；保留工作区已有及同时新增的题目增删／排序控件，不对比赛说明做全局反转义，不修改正式数据。
- [x] 已有实现：修正连续保存时发现的 `datetime-local` 六位小数回显被 Chromium 清空问题，使用浏览器支持的 UTC 毫秒表示；未改动时间在写锁内保留数据库原微秒值，实际修改仍按原服务层规则校验。共用 HTTP 和数据库结构不变。
- [x] 验收通过：先用真实 HTTP Chromium 复现首次保存后题号合并为字面量换行；修复后，Conda `minioj` 执行 `python -m pytest tests/test_management.py tests/test_management_browser.py -ra --tb=short` **99 passed（100.92s）**，无 skipped。新增 16 个浏览器组合覆盖根／`/minioj`、admin／system、UPCOMING／RUNNING、启用／禁用 JavaScript；每组连续三次保存，检查多题实际换行、说明空行／Unicode／字面量反斜杠加 n、题序及数据库值，并确认既有微秒时间不丢失、时间框不清空。原有时间锁回归同时验证实际改动 1 微秒／1 毫秒仍返回 409。
- [x] 验收通过：`python -m ruff check` 与 `python -m ruff format --check` 对本次三个 Python 文件通过，`git diff --check` 通过；architecture 同步表单回显／精度适配行为，操作步骤不变，未修改 README。

**交付边界：** 保留全部未提交修改；本节单独的表单修复验收不代表新增比赛功能完整验收，也未单独重建镜像／重启／部署正式服务或 commit/push。后续本页 Contest 增量已完成全仓 **622 passed**，包含这里新增的 16 个浏览器组合；新验收镜像中的时间精度适配与当前模板／路由 SHA-256 已核对一致，包含本次修复。更早的收尾镜像／555 项记录仍只代表修复前的历史证据。

## Contest 增量：Performance 与随时编辑题目（2026-10-02）

范围仅为最新附件的两项目标；保留当前全部未提交实现／用户数据，不推进 Phase 5，不引入永久 rating、冻结榜、虚拟参赛或新赛制。当前实现与验收分开记录；后面的开赛题目锁／无 rating 描述为先前版本的历史证据，以本节和 [contest](docs/contest.md) 的新规则为准。

- [x] 已有实现：[contest_performance.py](src/minioj/contest_performance.py) 纯函数，输入当前题目 rating 与当前 AC 状态，使用难度加权 logistic 似然及 0–4000 二分搜索，null rating=1200。普通同斜率无权重 MLE 仍只看 AC 数量，故明确使用 `w=1+d/400`。空列表／0 AC／全 AC／极端 rating 均有有限整数边界，不修改 Problem 原值或 User。
- [x] 已有实现：同一动态 standings 服务同时计算排名格子和 Performance，保留 solved DESC／penalty ASC／原并列规则。Practice、旧 JudgeRun、未完成结果不当作有效 AC；当前题目增删／顺序、rating 和重判立即随下一次请求重算。
- [x] 已有实现：admin/system 通过原权限、CSRF 和比赛写锁随时修改 ContestProblem（开赛／结束／有提交均允许），仅保留原比赛时间锁和 RUNNING 整场删除限制。删除关联保留题目、原提交和历史，重加恢复比赛时间窗内结果；既有新提交守卫拒绝已移除题目，不取消已接收评测。
- [x] 已有实现：榜单新增整数 Performance 列；管理表单本地 Add problem／Remove／↑／↓，无 JS 仍逐行编辑题号，修复多题号字面 `\\n` 回显。最多 100 题／唯一性／可用性由后端检查，不引入 UI 依赖。
- [x] 已有实现：此前只有 HTML 榜单，补公开 `GET /api/v1/contests/{id}/standings` 和显式响应模型，不增重复管理写 API。可选 Session／Bearer、原错误包络及 UPCOMING 可见性；普通未开始榜单按可见空题目列表显示 Performance 0，不泄漏比赛关联。不改双方共用 HTTP、提交、Token 或 Feedback Mode。
- [x] 验收通过：新增 [test_contest_performance.py](tests/test_contest_performance.py) **51 passed（53.63s）**，包含四题全部 16 种 AC 状态／顺序不变／难题交换、多解题单调、null／空／极端边界；三角色 × 三比赛状态、CSRF、无效列表原子性、100／101 题、移除后新提交／已排队提交、增删／重加／空列表、当前重判与 history／practice／pending／时间窗隔离、排名逆序／并列、JSON／OpenAPI／可见性，以及真实 HTTP Chromium 的 admin/system × 桌面／手机 × 根／子路径和无 JS 回退。截图已实检。
- [x] 验收通过：示例 `[800,1200,1600,2000]` 按顺序 AC 0–4 题得到 **400、949、1370、1791、2400**；同为 1 AC，解 2000 得 **1275 > 949**。取整／限幅时极小变化允许同分，算法不是官方 Codeforces rating。
- [x] 验收通过：`python scripts/smoke_test_management.py` 在真实 Docker 及临时 DB/data/jobs 中通过根／子路径 WA → AC → WA → AC、800／1600 Performance 与 JSON 同步、实时增删／排序／重加、身份／源码／原时间／历史保留、个人 solved 不被移除比赛关联影响；CE → Rejudge → AC 仍通过，owner 容器和临时数据清理通过。
- [x] 验收通过：全仓 Ruff lint／format **107 文件**、8 个应用 JS `node --check`、Compose 解析、差异检查；离线 wheel 构建和新模块／模板／静态资源打包核对通过。
- [x] 验收通过：标准 Dockerfile 通过独立临时 BuildKit，从官方 Python 基础镜像 **无缓存下载全部依赖构建**，专用镜像 `minioj-contest-server:20261002-performance`，摘要 `sha256:f0b0d5d3b302871607d892fac0f1a72c15335fee1436dd6b7995349b61b83782`。无网络／只读容器验证算法、模块／模板／静态文件／OpenAPI、无 `.env`／个人题库／DB／代理残留；8 个业务／界面文件 SHA-256 与当前工作区逐一一致。不把构建检查称为正式部署。
- [x] 验收通过：最终 `conda run --no-capture-output -n minioj python -m pytest -ra` **622 passed（505.58s）**，无 skipped，包含当前 MiniOJ／cf_import／浏览器／迁移回归和最终 Performance 实现。最后补充真实 Docker 脚本后再次复验全仓 Ruff lint／format（107 文件）和差异检查通过。

README 双语、architecture 和 contest 规则同步；本次无新配置／数据库表或迁移。默认 Docker daemon 的旧失效代理未修改；临时 BuildKit／匿名缓存／注册、构建配置和 wheel 目录已清理，保留上述独立验收镜像。未将本轮构建外推为新镜像的完整 Nginx 部署验收；未部署／迁移正式环境，不自动 commit/push。

## 收尾与网页体验（2026-10-02）

- [x] 已有实现：测试数据预览严格保留前 1024 UTF-8 字节，实际超出时在网页末尾加 `...`；文件最多读 1025 字节判断是否结束，已保存 failure 沿用截断标志。恰好／不足上限不加，Unicode 不产生破碎字符；机器 Feedback 内容及隐藏数据权限不变。
- [x] 已有实现：提交详情本地 C++20 轻量高亮与 Copy code，复制完整纯源码而非 HTML；拒绝剪贴板／普通 HTTP 时回退，失败可手动复制。源码只用安全 textContent 创建节点，不执行代码、不增加 CDN／源码权限。
- [x] 已有实现：练习／比赛／管理 std 编辑器 Tab 四空格、选中多行缩进及 Shift+Tab 反缩进；原生 undo 与 setRangeText 回退，Esc 后 Tab 可离开编辑器；不截获 Custom input／普通表单 Tab。
- [x] 验收通过：新增 [test_code_ui.py](tests/test_code_ui.py) **29 passed（29.91s）**：12 个文件／stored-failure 字节边界与 Unicode 检查、17 个真实 HTTP Chromium 流程，包含根／子路径、手机／管理、XSS 字符不解析、真实剪贴板与拒绝／非安全／缺失／失败回退、缩进／撤销／实际提交源码。已有逐点／权限／模式回归随全量通过，源码预览桌面／手机截图已实检。
- [x] 验收通过：`conda run --no-capture-output -n minioj python -m pytest -ra` **555 passed（454.07s）**，无 skipped；包含当前 MiniOJ、cf_import 与全部新网页检查。此前 cf_import 静态缺口已由后续实现修正，本轮复验 `python -m ruff check .` 和 `python -m ruff format --check .` **105 文件全部通过**，未重写导题业务。7 个应用 JS `node --check`、Compose、systemd unit 和差异检查通过。
- [x] 验收通过：仓库标准 Dockerfile 从官方 `python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f` **无应用缓存从零构建**，下载并安装全部依赖（包括 SQLAlchemy 2.1.1、FastAPI 0.142.2、Starlette 1.7.0、Pillow 12.3.0），没有叠加既有 MiniOJ 依赖镜像。验收镜像保留为 `minioj-remaining-server:20261002-ui`，摘要 `sha256:f148dfdee05f6777b8d4f5659fc4a25997eaf261a4cbbf838ad484518e46227d`；未替换正式 tag。
- [x] 验收通过：`python scripts/smoke_test_phase3_deploy.py --server-image minioj-remaining-server:20261002-ui --polygon-package 'store/graph-1-27$linux.zip'` 使用该干净镜像完成隔离 Nginx／Server／宿主 Worker：真实 Tab／焦点离开、源码高亮／完整复制、Token、Sample／Custom Run、AC/CE/TLE／轮询；26 MiB Polygon 导入、34 例主 std AC；独立客户端根／子路径及故障 503 均通过。临时部署栈、owner 容器／Job 清理通过。
- [x] 已有实现并验收：Docker context 排除运行时头像／题库及既有 DB／Job／`.env`，gitignore 新增本地头像；不删除磁盘数据。无网络独立镜像检查确认资源／新模块存在、无代理环境残留、无个人数据／数据库／`.env`。保留全部既有修改、题库、头像、CF 缓存和源码。

**环境边界：** 默认 Docker daemon 仍指向不可达的 `127.0.0.1:7897`，普通构建与 legacy builder 均实际失败；直连也不可用，但宿主现有代理可访问 Registry／PyPI。本轮使用独立临时 BuildKit 容器、只读公共 CA、私有临时 Unix socket 和临时 buildx 配置完成标准构建，没有修改共享代理、daemon、正式镜像／服务或 Dockerfile。该临时构建器／匿名缓存／注册及目录已删除，保留上面的验收镜像供后续选择。README 双语、architecture 和本 TODO 同步；未 commit/push、未正式部署／迁移，也未把“收尾”扩展为 Phase 5 协议冻结。

## 新功能 M1–M7（2026-10-01）

以下保留该阶段的历史证据；最新全仓／干净构建复验见上方 2026-10-02 收尾记录。

依据新功能附件，在现有 Phase 4 工作区增量实现；Phase 5 协议工作仍保留原有状态。权限、比赛和重判规则见 [permissions](docs/permissions.md)、[contest](docs/contest.md)、[rejudge](docs/rejudge.md)。

| 里程碑 | 已有实现 | 验收通过（范围见下方实测） |
| --- | --- | --- |
| M1 | [x] 三角色、集中能力、roles-v2 一次迁移、bootstrap system、自保护 | [x] 角色矩阵／伪造访问／CSRF／旧库保留／重复迁移 |
| M2 | [x] 红色管理入口、独立 Sidebar Console、Dashboard、题目／七项提交筛选、system-only 页 | [x] 三角色后端与导航、桌面／手机、根／子路径、旧入口兼容 |
| M3 | [x] 单次 Rejudge、逐次 JudgeRun、共享 Worker、generation 防过期写入与审计 | [x] 权限／GET／活动状态／并发／429 原子性／旧 Worker／历史策略，以及真实 Docker 重判 |
| M4 | [x] 公开主页、distinct current AC、首次有效 AC、统计、默认与安全本地头像、Settings | [x] PNG/JPEG/WebP／伪文件／路径／大小／像素／替换／commit 回滚；浏览器上传与隐私 |
| M5 | [x] 公开 Contest、UTC 状态、排序／报名／CRUD／比赛提交复用 Submission | [x] 精确起止边界、题目归属、角色／CSRF、报名幂等、微秒编辑、开赛锁与软删除 |
| M6 | [x] ICPC 动态排名、20 分钟罚时、个人统计与 Rejudge 双向联动 | [x] 所有罚时 verdict、CE/IE 排除、时间窗／首次 AC／practice 隔离／并列，以及真实成绩增减 |
| M7 | [x] 迁移、安全、浏览器／Docker／旧 HTTP 回归脚本及升级文档 | [x] 下列应用范围实测；不代表正式升级、干净 Server 构建或 Phase 5 |

**M1–M7 实测证据：** Conda `minioj`、Python 3.12.14、pytest 8.4.2、Ruff 0.16.9、Pillow 12.3.0、Chromium 153.0.8010.12。

- `pytest -ra`：**469 passed（338.36s）**，包含既有 385 项与本次新增 84 项；无 skipped。新增业务边界针对性 67 项通过；浏览器＋旧库迁移针对性 17 项通过。
- [test_management.py](tests/test_management.py) 覆盖权限／重判／历史策略／头像／比赛／统计；[migration](tests/test_management_migration.py) 用真实旧表结构验证原账号、源码、成绩、时间及 id 保留；[browser](tests/test_management_browser.py) 经真实回环 Uvicorn（非请求拦截）验证三角色、1365/390 宽度、根／`/minioj`、确认重判、头像和比赛提交，无页面脚本错误或横向溢出。
- `python scripts/smoke_test_management.py`：当前真实 Docker Judge，根／子路径 WA → AC → WA → AC，初始＋三次历史、身份不变、当前 testcase／revision、个人 solved／standings 增减通过；独立编译预算故障 CE → Rejudge → AC。临时 DB/data/jobs 与 owner 容器清理通过。
- `python scripts/smoke_test_phase3_deploy.py --server-image minioj-m7-server:n3bvms --polygon-package 'store/graph-1-27$linux.zip'`：独立 Compose/Nginx＋宿主 Worker；Chromium 经新 `/manage/problems/import` 上传 26 MiB 包、34 例／1 图片、sample 与主 std AC；原 Token、Custom Run、AC/CE/TLE、自动轮询、故障 503、Web health 与清理通过。无 MiniOJ import 的独立客户端分别经代理子路径／直连根路径完成 Phase 4 原 HTTP 流程，不改共用契约。
- 测试 Server 镜像 `sha256:3f3d3a91b13e0c2f268e631db7a94d055718f90e44b3fbd8bd093b925ba74205` 为现有依赖层＋Pillow＋当前源码的隔离叠加，已删除；Judge 镜像 `sha256:1e126bd3685cd55f15c6027c2bd64419b7c822a37132ab279475847c11aedd41`。不把叠加镜像称为从零构建。
- `ruff format --check src tests scripts`（76 个文件）、`ruff check src tests scripts`、六个应用 JS `node --check`、`docker compose config --quiet`、`systemd-analyze --user verify deploy/minioj-worker.service`、`git diff --check` 均通过。离线 `pip wheel --no-deps --no-build-isolation --no-index` 成功，核对新模块／模板／默认头像与 Pillow metadata 随包分发；临时 wheel／构建目录已清理。
- 当时全仓 `ruff ... .` 另有并行新增 `store/cf_import` 的 10 个格式／4 条 lint 问题，未改动这些范围外文件。**2026-10-02 已复验后续修正，全仓通过**，不再作为当前缺口。

**交付边界／下一步：** 未自动 commit/push，未部署或迁移正式库，未推进 Phase 5。现有脏工作区和 `data/problems/T1003/` 保留。**从零 Server 构建与全仓静态检查缺口已于 2026-10-02 关闭，见上节**；正式应用仍须按 README 协调停止旧 Web／Worker、备份、幂等迁移及启动新版。冻结榜、私密比赛、rating、批量重判及在线系统配置不在本次 V1 范围。

## 阶段证据

- **[x] 已有实现**：已静态阅读代码，确认存在所述实现；不等于运行成功或整个阶段验收通过。
- **[x] 验收通过**：本轮已记录环境、命令和实际结果；只代表所述范围，不外推到其他阶段或正式部署。
- **[ ] 待实现／待补齐**：没有实现证据，或与规划存在差距。
- **[ ] 待验证**：实现或测试文件存在，但本轮没有运行；必须记录环境、版本、命令、结果和日期才能勾选验收项。
- **Phase 3 基线实测（2026-10-01）**：Conda `minioj` 环境，Python 3.12.14、Ruff 0.16.9、pytest 8.4.2；Ruff 57 个文件、217 项 pytest（93.85s）、六个应用前端脚本、Compose、systemd unit 和差异校验通过。新增扩展的最终全量证据见下节。
- **Phase 2 真实 Docker 实测（2026-10-01）**：Docker Client／Server 29.8.1，镜像 `sha256:1408202922ade7964b2f82abd4ebb853ce63e2baf0cbdb14fcf9e306f5952a95`。栈冒烟通过 Custom Run 和 API → Submission → Worker → Docker → Feedback；Judge 冒烟通过 AC、WA、CE、RE、TLE、MLE、OLE、主动退出 124/137、有限短输出和编译输出超限／截断；Sandbox 冒烟通过网络、PID、宿主文件、只读根目录和容器清理；Worker 故障冒烟通过损坏／缺失 Testcase、不可用镜像的安全 IE 及 Web healthz 200；generator → std 两例复验通过。临时数据已清理。
- **Phase 3 隔离部署实测（2026-10-01）**：Playwright Chromium 153.0.8010.12 在临时数据库、data、端口和独立 Compose project 中，经 Nginx + Server + 宿主 Worker 的 `/minioj/` 完成登录、网页创建 Token、Run Sample、Custom Test、Submit、自动轮询到 AC；独立 Bearer 客户端完成 CE、TLE 和查询。删除临时镜像别名注入 Docker 故障后 Custom Run 返回安全 503，Web health 保持 200；Custom Run 未创建额外 Submission，容器、Job 目录、Compose 容器／网络和临时数据均已清理。
- **Phase 4 实测（2026-10-01）**：完整 `pytest` 385 passed（179.09s）；Phase 4 及相关回归 108 passed（48.18s），最后补入双源码字段总量边界后，`tests/test_phase4.py tests/test_api.py` 28 passed（12.70s）。Playwright 创建初始 Token 后，不 import MiniOJ 的标准库 HTTP 客户端分别经 `/minioj` Nginx 子路径和直连根路径完成身份、题目、Token 创建／列出／软撤销、Custom Run、Submission、轮询和 AC；撤销后密钥为 401 且 metadata 保留。默认干净 Server 镜像构建两次因下载 SQLAlchemy wheel 分别超过 240／600 秒，未记为通过；随后复用本地已安装依赖层、仅叠加当前源码的临时镜像完成行为验收并删除。正式 Compose、镜像和数据未修改。
- **此前验证**：公式标记／本地资源回归与 KaTeX 0.18.10 Node 显示公式／MathML 冒烟、真实 Chromium 编辑页检查，以及 generator → std 两组真实输入／输出均通过；Phase 0 时已确认真实 `.env` 被忽略且未跟踪。
- **隔离启动实测（2026-10-01）**：在 `/tmp` 临时目录连续两次执行 `minioj init-db`，确认 6 张业务表和独立 data/job 目录；以 Uvicorn 绑定 `127.0.0.1:18765` 后，`GET /`、`GET /healthz`、`GET /static/style.css` 均为 HTTP 200，随后正常停止并删除临时目录。

本轮开始时 HEAD 为 7940c7a；工作区已有 README.md、README_zh.md、restart、database.py、models.py、server/web.py、test_restart.py 的未提交修改，以及新增 tests/test_username_case.py。本轮保留并复验这些工作，没有回退或重建骨架；新增的 Phase 0 改动另见本节任务。

旧 TODO（2026-09-30）记录：基线 711bc3b 通过 Ruff 和 95 项 pytest，以及宿主机 TestClient → Worker → Docker → Feedback。保留为**历史验证记录**；当前已以新实现重新执行 Judge 和隔离冒烟，但仍不等于 Compose 部署或独立 HTTP 客户端已经验收。

| 范围 | 实现证据 | 自动化证据 |
| --- | --- | --- |
| 基础 | [pyproject.toml](pyproject.toml)、[config.py](src/minioj/config.py)、[database.py](src/minioj/database.py)、[入口](src/minioj/server/main.py)、[CLI](src/minioj/cli.py) | [配置测试](tests/test_config.py)、[测试入口](tests/conftest.py)、[升级测试](tests/test_database_upgrade.py)、[子路径测试](tests/test_subpath.py)；本轮均随 pytest 通过 |
| 用户、题目 | [模型](src/minioj/models.py)、[题目存储](src/minioj/problems.py)、[构建队列](src/minioj/testcase_builds.py)、[Markdown／TeX 解析](src/minioj/rendering.py)、[公式渲染](static/math.js)、[上传解析](src/minioj/server/uploads.py)、[Web](src/minioj/server/web.py) | [编辑／软删除／历史保留](tests/test_problem_lifecycle.py)、[Phase 1 回归](tests/test_phase1.py)、[std／generator](tests/test_testcase_builds.py)、[题目功能](tests/test_problem_features.py)、[升级测试](tests/test_database_upgrade.py) 等随当前 pytest 通过；编辑页 Chromium 和 KaTeX Node 冒烟通过 |
| Judge | [状态／结果契约](src/minioj/judge/contracts.py)、[Worker](src/minioj/worker/main.py)、[DockerJudge](src/minioj/judge/runner.py)、[Checker](src/minioj/judge/checker.py)、[镜像](docker/cpp20/Dockerfile) | [契约](tests/test_judge_contracts.py)、[Judge](tests/test_judge.py)、[Docker](tests/test_docker_runner.py)、[Worker](tests/test_worker.py) 随 pytest 通过；Judge、Sandbox、Worker 故障三个真实冒烟通过 |
| Web、Token、API | [API](src/minioj/server/api.py)、[schema](src/minioj/schemas.py)、[错误包络](src/minioj/server/errors.py)、[统一反馈](src/minioj/feedback.py)、[独立客户端](scripts/smoke_test_phase4_api.py) | Web／Token／API 与 [Phase 4 契约测试](tests/test_phase4.py) 随 pytest 通过；栈冒烟和真实 Chromium 隔离部署通过，独立客户端根路径／代理子路径均通过 |
| 部署 | [Compose](compose.yaml)、[Nginx](deploy/nginx.conf)、[Worker unit](deploy/minioj-worker.service)、[restart](restart)、[README](README.md)、[中文 README](README_zh.md) | restart 测试、Compose 解析、systemd unit 校验及隔离 Nginx + Server + 宿主 Worker 全流程通过；未部署正式环境 |

## 本轮新增调整：CPU 计时与 Polygon 上传（2026-10-01）

- [x] 已有实现：运行使用 cgroup v2 CPU 毫秒及明确超时标记；独立 CPU／墙钟／Docker watchdog，进程树统计、报告保护及后代清理；旧镜像拒绝，编译预算和历史结果保留。
- [x] 验收通过：6 次极短程序原宿主记录 226–251 ms，新记录均为 1 ms；300 ms 睡眠 + 80 ms 计算为 85–86 ms，4 个后台 CPU 负载前后中位数差 0 ms；子进程 TLE（204 ms）、UID 1000／零 capabilities、报告防篡改和 setsid 后代清理通过。全部 verdict、主动退出 124/137、编译／运行输出边界和网络／PID／宿主文件隔离冒烟通过。
- [x] 已有实现：Admin 选择 Polygon ZIP、语言及可选题号；HTML／图片／主 std／限制／sample／generated 导入，支持 wcmp 和 nyesno；有界上传／展开、路径与 XML 校验、批量事务及文件回滚；不执行包内资源、不覆盖旧题。
- [x] 验收通过：最终全量 `pytest` 252 passed（105.13s）；Ruff 60 个文件、lint、六个前端脚本、C `-Wall -Wextra -Werror` 语法检查、Compose 配置及差异校验通过。导入覆盖权限、CSRF、zip 路径／symlink／重复／超限／DTD、缺失答案、未知 checker、hidden／std 隐私、root_path 图片访问／删题隐藏和 commit 回滚。
- [x] 实际包验收通过：Chromium 经隔离 `/minioj/` Nginx 选择并上传 `store/graph-1-27$linux.zip`（26 MiB），正确导入中文题面、2000 ms／1024 MiB、34 例／1 sample／1 图片；图片、Run Sample、全部 34 例主标准解 AC、原有 AC/CE/TLE、Docker 故障 503 和清理均通过。
- [x] 修复并复验：初次完整评测第 31 例 RE（exit 139）源于深递归受默认 8 MiB 栈限制；执行栈改为题目内存预算后全部 34 例 AC，总 RAM 仍受 cgroup 限制。镜像标签、通信 watchdog 和栈配置新增回归随 54 项针对性检查通过。
- [x] 干净镜像构建验收（2026-10-02）：早先的下载超时／失效 daemon 代理证据保留；现已用独立临时构建器从官方 Python 镜像无应用缓存完整安装依赖，并以新镜像通过隔离部署。默认代理未更改，具体范围见本文件顶部收尾记录。

## 本轮新增调整：testlib Checker（2026-10-01）

- [x] 已有实现：查阅并固定官方 `MikeMirzayanov/testlib` 到 `1e4e8a24c79c6bad3becbdb5a332ffc352b7d5dd`，内置 header、21 个 checker 源码和许可证；其中 19 个非评分 checker 可导入，2 个评分 checker 明确拒绝。包内 C++ 源码优先，支持自定义多解 checker、本地头文件和缺失 header 回退；只编译源码，不运行上传二进制。
- [x] 已有实现：Problem 新增 checker_name／checker_bundle／checker_sha256 三个内部 nullable 列，幂等升级旧库、保留 lines／tokens／yesno 及历史结果；源码包 4 MiB／128 文件、单头文件 1 MiB、入口沿用源码上限，读取资源前提前检查累计上限；安全路径和摘要校验，checker 元数据随导入事务保存，不向普通／Agent API 暴露。
- [x] 已有实现：Worker 快照 checker；每个提交在独立 job 编译一次，再逐例用独立只读 Docker 沙箱运行；默认 checker CPU 10000 ms／内存 512 MiB，不计入选手资源；0 → AC、1/2/4/8 → WA，失败／崩溃／超限／评分结果 → 安全 IE，诊断仅内部日志；结束和中断恢复均清理 checker job。Run Sample 提示仅执行，Submit 使用 checker；共用 HTTP 未变。
- [x] 针对性验收通过：最终源码包累计上限和路径回归后，`pytest tests/test_testlib.py tests/test_polygon.py tests/test_database_upgrade.py` 90 passed（36.98s），覆盖所有标准导入、包内源码／header 优先、非 C++／评分／未知无源码拒绝、SHA、路径／数量／单文件／总量限制、退出码与错误分类、选手／checker job 隔离、源码隐私、Worker 快照／摘要损坏和数据库幂等升级。
- [x] 真实 Docker 验收通过：`python scripts/smoke_test_checkers.py --polygon-package 'store/graph-1-27$linux.zip'`，Judge 镜像 `sha256:1e126bd3685cd55f15c6027c2bd64419b7c822a37132ab279475847c11aedd41`。19 个非评分标准 checker 分别编译、AC／WA 通过（含浮点容差、无序序列、逐行比较、Case 格式）；自定义 C++＋本地 header 接受替代答案／拒绝错误答案，验证 UID 1000、答案不在选手 job／选手私有文件不在 checker job，选手 CPU 两次均 2 ms；checker 编译失败／主动 FAIL／CPU 超时为 IE；实际 graph 包原始 nyesno 源码＋header、34 例主 std 全部 AC，job／owner 容器已清理。
- [x] 浏览器／部署链路验收通过：Chromium 在隔离 `/minioj/` Compose/Nginx＋宿主 Worker，真实选择 graph ZIP，导入 34 例／1 图片；样例展示 execution-only、全部 std 正式提交 AC，以及原有 Token／Custom Run／AC／CE／TLE、Docker 故障 503 与清理均通过。基于现有依赖临时叠加源码的测试 Server 镜像，不视为干净构建证据；该临时镜像已删除，正式服务／镜像未修改。
- [x] 打包验收通过：`pip wheel --no-deps .` 成功，wheel 包含 testlib header、LICENSE、UPSTREAM 及 21 个 checker 源码；23 个源码／许可证与固定上游文本一致（仅换行规范化）。Ruff format/lint、6 个应用 JS、Compose 和差异检查通过。
- [x] 最终全量验收通过：最后一处累计读取上限补齐后，完整 `pytest` **326 passed（146.40s）**；此前两轮分别 324 passed（156.31s）和 325 passed（164.00s）。最后 Ruff format 60 个文件／lint、6 个应用 JS 和差异校验通过；最终 wheel 构建成功，SHA-256 `def41834595cad524d8c173ba8f5f4e5447dd45d69a8e786fb3fffcf96f4ac26`。
- [ ] 截图包复验：`dyeyourname-28$linux.zip` 本轮不在 `store/`，不能宣称其已实测；放入后可运行上述 checker 冒烟并指定该包。交互／评分题仍不支持，部分分数协议及样例 checker API 留待明确；此项不影响下文已独立完成的 Phase 4。

## 本轮新增调整：提交逐数据点展示（2026-10-01）

- [x] 已有实现：提交汇总区逐点显示 AC／WA／RE／TLE／MLE／OLE／IE、运行 ms、内存峰值 KB；新记录使用 CPU 时间，旧记录保留原计时值，表头用 Run time。状态行读取策略过滤后的历史 test_results，不用汇总最大值或当前用例数量伪造测量；最新样例／非样例分组及预览规则见下方。
- [x] 已有实现：保留 0、未知测量显示 —；首错即停后的后续点为 Not run，缺失 metadata 为 Not recorded／旧记录提示重新提交；长表固定表头、区域滚动和键盘操作，支持手机。full／diagnostic 仅安全 metadata，verdict_only 隐藏表格；权限、轮询／FINISHED 刷新、数据库、Judge 与共用 HTTP 不变。
- [x] 针对性验收通过：`pytest tests/test_submission_results.py tests/test_web.py tests/test_api.py tests/test_subpath.py` 33 passed（20.25s），覆盖 34 例 AC、逐点而非汇总资源、0／null、各失败状态、CE／未运行、旧结果／异常 metadata、源码隐私／Feedback Mode、所有者／Admin 权限、root_path；Chromium 桌面／手机＋根路径／子路径 4 组自动轮询到 34 行并无页面溢出或脚本错误。
- [x] 全量验收通过：补充键盘滚动与截图检查后，完整 `pytest` **352 passed（163.58s）**；Ruff format 66 个文件／lint、`node --check static/submission.js` 和差异检查通过。Chromium 桌面／手机＋根路径／子路径 4 组自动刷新到 34 行、区域键盘滚动、页面不溢出且无脚本错误；截图实检确认状态与独立资源对应。随后压缩手机表格列间距，使 390 px 视口同时看到全部四列；`pytest tests/test_submission_results.py -k browser` **4 passed（6.80s）**，含完整四列无需横滚、键盘滚动、自动刷新和截图实检。最后补充历史运行时间说明，原针对性命令再次 **33 passed（21.08s）**；不部署／不 push，不自动重判旧成绩。

## 本轮调整：样例／测试点分组与预览权限（2026-10-01）

- [x] 已有实现：`Sample results` 展示 sample，`Testcase results` 展示 hidden/generated 或无法可靠分类的旧点；保留原始编号，空组不渲染。普通用户看到全部点的状态、逐点时间／内存和 Not run，不再过滤非样例 metadata 行。
- [x] 已有实现：后端只为有权限的数据附带预览；full 下普通用户仅能展开样例，admin/system 可展开非样例。普通用户既不收到非样例 input/expected/actual，也不读取这些文件；预览仍为前 1024 字节，题目 revision 变化后不读取当前 testcase。diagnostic 无任何预览，verdict_only 仍不显示结果表；共用 HTTP、Submission 所有权和 Judge 逻辑不变。
- [x] 针对性验收通过：`pytest tests/test_submission_results.py tests/test_management_browser.py tests/test_management.py tests/test_worker.py -ra` **128 passed（128.93s）**。包括真实回环 HTTP Chromium 的 user/admin/system × 桌面/手机 × 根/子路径 12 组：分组标题与原编号、非样例状态、数据未嵌入、样例／管理员非样例预览、点击／Enter 收起、无页面溢出或脚本错误。实检桌面普通用户与手机管理截图。
- [x] 静态验收通过：本轮修改的 `feedback.py`、`server/web.py`、`test_submission_results.py` Ruff lint／format（三文件）、`node --check static/submission.js`、`git diff --check`。README 双语操作说明及 architecture 已同步。
- [x] 最终全量验收通过：新增三角色 × 三种 Feedback Mode、空组、未知旧分类及“普通用户不读取非样例文件”回归后，`python -m pytest -ra` **519 passed（392.69s）**，无 skipped。首次 `pytest -ra` 被并行新增的 `test_cf_import.py` 导入 `scripts` 的入口路径问题阻断；使用 Python 模块入口加载仓库路径后全量通过，无需修改这些无关文件。本轮未重判旧成绩、未改 API／Judge／数据模型，未部署／重启正式服务，未自动 commit/push。

## 本轮新增调整：身份接口反馈模式（2026-10-01）

- [x] 已有实现：`GET /api/v1/me` 新增 `feedback_mode`，从 Server 生效的 `MINIOJ_FEEDBACK_POLICY` 读取，响应模型限定 full／diagnostic／verdict_only；Session 与 Bearer 使用相同值，账号权限保持独立。
- [x] 配置说明：OJ 根目录 `.env` 和 `.env.example` 标注三种模式及重新加载方式，当前本地配置为 full；Compose 沿用已有环境映射。README 双语及 architecture 已同步。
- [x] 针对性验收通过：Conda `minioj` 下 `python -m pytest -ra tests/test_phase4.py tests/test_api.py tests/test_tokens.py tests/test_config.py tests/test_subpath.py tests/test_username_case.py` **88 passed（60.86s）**，覆盖三种模式的 Session／Bearer 身份响应、OpenAPI 枚举和既有配置／API／Token／子路径／用户名回归。受限沙箱在 TestClient／AnyIO 线程启动处阻塞，停止后使用独立临时数据库在沙箱外通过；未启动或重启正式服务。
- [x] 静态与配置验收通过：修改的三个 Python 文件 Ruff lint／format、`git diff --check`、`docker compose config --quiet`；独立进程读取未设置及三种环境值均符合预期。`.env` 仅增加反馈说明注释，原有配置值完整保留；本轮不代表 Phase 5 全部完成。

## Phase 0：项目基础

**目标：** 在现有代码上确认可初始化、可启动、可测试的 MiniOJ 基线。

**依赖：** 本轮仓库检查及架构文档；已满足。

**任务：**

- [x] 已有实现：pyproject.toml、依赖和 CLI；environment.yml 提供 Python 3.12 Conda 环境，包要求 Python >=3.11。
- [x] 已有实现：FastAPI 入口、环境变量配置、SQLAlchemy/SQLite、首页、Jinja2 模板和静态文件入口。
- [x] 已有实现：minioj init-db、启动初始化和 Token preview 兼容升级；当前工作区含用户名大小写唯一索引及冲突检测。
- [x] 已有实现：.env.example、忽略真实 .env 的 .gitignore、pytest 入口、中英文启动说明。
- [x] 本轮补齐：新增 `MINIOJ_JOB_DIR`；未设置时兼容原 `MINIOJ_DATA_DIR/jobs`，宿主 Worker 可显式使用 `/tmp/minioj/jobs`。`.env.example` 补齐源码、Custom Run 输入、合并输出和 Token 默认期限设置，Compose 显式转交 Server 使用的运行配置。
- [x] 本轮补齐：Server 导入时要求非占位且至少 32 字符的 `MINIOJ_SECRET_KEY`，错误不回显 Secret；README 明确宿主进程不会自动读取 `.env`、每个终端的导入方式，以及 Compose 只使用显式映射。
- [x] 验收通过：空库和重复初始化经隔离 CLI 实测；旧 Token preview 升级、账号保留、用户名大小写索引幂等和冲突拒绝由自动化测试通过。
- [x] 验收通过：此前隔离 Uvicorn 的首页、健康检查、静态资源均返回 200；最终扩展工作区 Ruff、252 项 pytest、Compose 配置解析及差异检查通过。

**交付物：** 可运行基础、配置样例、数据库初始化／升级入口、基础测试、起步说明。

**验收标准：**

- [x] 验收通过：新隔离目录可重复初始化和访问首页；升级测试保留账号与 Token，大小写冲突不自动合并或改名。
- [x] 验收通过：Web/Worker 共用 `Settings` 和 `MINIOJ_*` 命名；示例只含占位 Secret，真实 `.env` 被忽略且未跟踪，Secret 校验错误不披露输入值。

**范围边界：** Phase 0 本地基线通过不代表真实 Compose 部署、Docker Sandbox、Custom Run 部署链路或外部客户端联调通过；这些仍留在对应后续阶段。

## Phase 1：用户与题目

**目标：** 完成注册登录、人工建题、测试数据管理和权限隔离。

**依赖：** Phase 0 基础可用。

**任务：**

- [x] 已有实现：User、Argon2、注册／登录／登出、签名 Session、CSRF、个人信息和密码设置。
- [x] 已有实现：user/admin 检查、CLI／环境变量创建 Admin、用户角色和启停管理；公开注册只创建普通用户。
- [x] 已有实现：Problem CRUD、来源／rating／tags、题目列表和详情、基础 Admin 页面。
- [x] 已有实现：sample/hidden/generated、TestCase 文件存储及路径检查；表单／JSON 文本添加用例，公开样例另存 Sample。
- [x] 已有实现：账号校验、部分 CSRF、SQL 注入输入、Token 名 HTML 转义和页面渲染测试。
- [x] 本轮补齐：TestCase 增加 `created_at` 及输入／输出 SHA-256；现有 `order` 明确作为规划中的 testcase index，并继续由每题唯一约束保证顺序不重复；旧库升级幂等，旧文件的校验和允许暂为空。
- [x] 已有兼容入口：Admin JSON／旧 Web 路由仍可直接提供 input/output，不破坏已有管理调用；文件使用唯一名称和原子替换，新增／更新／整题删除的数据库失败路径有回滚保护。
- [x] 本轮扩展：每题可上传一份仅 Admin 可见的 C++20 std；Web 上传 testcase 时只提供输入，Worker 在 Docker 中运行 std 自动计算输出。std 源码、SHA-256 和更新时间入库；有提交后仍允许更换，旧 std 构建任务不能写回结果。
- [x] 本轮扩展：Admin 可上传 C++20 generator，按 `argv[1]=seed`、`argv[2]=1-based index` 批量产生输入，再由 std 计算输出；构建任务异步入队，整批 Testcase 与完成状态原子提交，编译／运行／落库失败不留下部分用例。
- [x] 本轮实现：Problem ID 支持 3–80 位大小写 ASCII 字母、数字和连字符；题面字段使用禁用原始 HTML 的 CommonMark 渲染，支持 `$...$` 行内 TeX 和独立成行的 `$$...$$` 块级 TeX，Admin 可执行同一渲染器的 CSRF 保护预览。KaTeX 0.18.10 及字体本地托管，不依赖 CDN。
- [x] 按最新要求调整：已有提交仍允许编辑题目／std／Testcase；`Problem.revision` 与 `Submission.problem_revision` 保留用于评测一致性，不展示修改／删除警告，不保存完整历史题面版本。整题采用 `deleted_at` 软删除，保留数据库行、测试文件和历史成绩，隐藏题目并拒绝后续提交／管理写入，题号不可复用；仅打开不可用题目链接时显示“题目不存在”；Sample 仍精确同步。
- [x] 已有实现：已删除题目／提交／Admin 修改和删除警告及持久化关闭代码；其他应用警告仍可用 × 关闭当前页面。std 支持直接粘贴、源码回显与独立保存，同时保留文件上传；编辑页增加四个编号分区和快捷导航，输入文件／generator 构建分别呈现。
- [x] 验收通过：覆盖普通用户伪造 API／Web 管理请求、禁用用户、提交所有权、路径穿越、跨题读取、符号链接，以及新增／更新／删除事务失败时 metadata 与文件一致性。
- [x] 验收通过：以唯一标记验证 hidden/generated 不经题目页、普通／Agent API、静态目录和未授权管理入口泄漏；题面 Markdown 的内嵌 HTML／危险 URL、样例、源码、编译输出、摘要和失败详情均有转义回归。公式解析和本地静态资源有自动化覆盖，KaTeX 运行时有 Node 冒烟；真实浏览器视觉效果仍归入浏览器验收。

**交付物：** 用户和题目模块、管理入口、可上传的测试数据存储、权限与一致性测试。

**验收标准：**

- [x] 验收通过：浏览器流程覆盖用户注册登录、Admin 建题、上传 std、由输入构建 sample/hidden、由 generator 构建 generated；普通用户只能看到公开样例，不能取得 std、generator 或 hidden。
- [x] 验收通过：修改 URL、伪造管理请求和存储路径不能越权；历史提交在题目编辑／删除后仍保留，软删除失败整体回滚；最新无警告页面行为另行复验。
- [x] 历史验收通过：std 粘贴保存／回显、HTML 转义、Admin／CSRF／源码隐私、空白／超限／同时粘贴与上传拒绝及草稿保留均有自动化回归；此前真实 Chromium 验证警告鼠标／键盘关闭及刷新恢复、std 粘贴保存／文件上传回显，以及桌面和手机编辑页分区布局。题目生命周期警告现已移除，不把旧证据当作当前行为。
- [x] 历史验收通过（持久化警告功能现已移除，2026-10-01）：`pytest tests/test_alerts.py tests/test_problem_lifecycle.py tests/test_subpath.py tests/test_testcase_builds.py` 33 passed（20.15s），随后补充再次修改题目不恢复提示的回归；当时全量 `pytest` 264 passed（120.85s）、Ruff format 61 个文件／lint、`node --check static/alerts.js` 和差异检查通过。真实 Chromium 153.0.8010.12 加载隔离 TestClient 实际页面与静态资源，验证鼠标／键盘关闭、刷新／跨页面持久化、账号／提交／root_path 隔离、存储访问／配额失败回退及后续删除警告；无页面脚本错误。浏览器检查使用请求拦截，不访问正式服务，不启动 Worker 或部署。
- [x] 最新页面验收通过（2026-10-01）：页面／生命周期／Phase 1／std 构建／Polygon／子路径针对性 `pytest` 76 passed（39.81s）；随后全量运行 330 passed（157.90s），并补跑新加入的提交结果表与页面检查 38 passed（25.07s）。Ruff format 66 个文件／lint、6 个应用 JS 和差异校验通过。Polygon 初始导入（根目录／外层目录包）及后续题目编辑均无修改警告；真实 Chromium 153.0.8010.12 在根路径／`/minioj/` 验证提交列表／详情／Admin 无修改／删除警告，点击删题链接仅显示不存在，其他编译截断警告仍可鼠标／键盘关闭；旧成绩、排队／运行中版本保护、权限及软删除回滚通过。浏览器请求拦截加载隔离 TestClient 页面／资源，无页面脚本错误，不访问或部署正式服务。
- [x] 验收通过：自动化覆盖 std／generator 队列、seed/index 参数、失败状态和原子批量保存；题目修改后旧排队提交不使用新数据评测，运行中评测使用已读取数据；删题取消构建、更换 std 拒绝过期任务，同一 std 连续追加任务可成功。此前真实 Docker 冒烟通过 2 组 generator → std 输入／输出，本轮不重跑容器编译链路。

**范围边界：** Phase 1 的验收本身不外推到 Judge 或部署；正式 Submission Judge 与 Worker 恢复现已在 Phase 2 单独验收，反向代理、外部 HTTP 客户端和正式部署仍属于 Phase 3–5。

## Phase 2：Submission 与 Judge

**目标：** 独立 Worker 真实评测 C++20，可靠保存结果、隔离执行和清理资源。

**依赖：** Phase 1 的用户、题目和 testcase。

**任务：**

- [x] 已有实现：Submission、202 入队、QUEUED 条件更新 claim、COMPILING/RUNNING/FINISHED 流程和独立 Worker。
- [x] 已有实现：DockerJudge 集中封装 Docker、容器内 g++ C++20 编译、逐用例执行和 Checker；正式请求只入队。
- [x] 已有实现：禁网、非 root、CPU／内存／PID／时间限制、只读根文件系统、任务目录挂载、输出监测及正常路径清理。
- [x] 已有实现：八种 verdict 分支、结构化结果存储、基础设施异常映射 IE；Checker 保留行内差异。
- [x] 已有实现并复验：Judge mock、Docker 命令／故障测试和真实 Docker 冒烟；本轮扩展至七种 verdict、用户主动返回 124/137、有限短输出及编译输出超限。
- [x] 本轮补齐：Judge、Worker、模型默认值和 API 流程共用 `SubmissionStatus`／`Verdict` 枚举；最终写入校验非终态字段、FINISHED verdict 一致性和非 IE compile_result。
- [x] 本轮补齐：CompileResult 保存 success、exit_code、stdout、stderr、time_ms、可空 memory_kb、超时／输出／OOM 及逐流截断标志；judge_result 为每个已执行用例保存不含内容的结果 metadata，首个失败仍保留兼容 failure。
- [x] 本轮补齐并验证：进程退出后复查 stdout/stderr 合并大小，编译与有限短程序输出超限均记录 OLE／CE 和截断标志；字节计数不再受 UTF-8 替换字符长度影响。
- [x] Phase 2 基线验收：GNU timeout preserve-status 与宿主墙钟记录通过；本轮按新增要求改为 cgroup v2 CPU 计时和可信超时标记。主动返回 124/137 仍为 RE，OOMKilled 优先 MLE，多例取最大值；历史宿主墙钟结果保留。
- [x] 本轮决策并实现：V1 每个 Job 目录只允许一个 Worker；进程锁 + 条件 claim 防重入。启动时中断 Submission 终结为安全 IE、构建终结为 FAILED，不自动重跑；最终结果条件更新，不覆盖已终态结果。
- [x] 本轮补齐：claim／读取／Judge／落库外围异常纳入循环日志及恢复路径；持久化 IE 只含安全类别，内部文件路径、Docker 原因和 traceback 仅写服务端日志，Custom Run 503 也不回显内部诊断。
- [x] 本轮补齐并验证：容器带 Worker owner 标签，启动清理遗留容器及 judge／构建目录；正常清理失败升级为可观测基础设施错误。单元测试覆盖删除失败，真实冒烟确认正常及 verdict 失败路径无残留。
- [x] Phase 3 确认并实现：正式提交及 Custom Run 队列有独立可配置容量；过载返回 HTTP 429 + `Retry-After`。成功提交仍为 202，基础设施故障仍为安全 503，不增加公共轮询路由。
- [x] 本轮补齐：提交创建、claim、编译开始／完成、最终 verdict、恢复、Sandbox／Worker 异常日志均关联 submission/build 标识，不记录源码、Token 或 Secret。
- [x] 验收通过：Docker 29.8.1、镜像 `sha256:140820...2a95` 的受控实测覆盖资源边界、隔离、清理、损坏／缺失 Testcase、不可用镜像及 Web 健康检查。

**交付物：** 独立 Worker、C++20 镜像、完整评测路径、结果结构、恢复及受控测试记录。

**验收标准：**

- [x] 验收通过：正确程序 AC、错误答案 WA、编译错误 CE、用户崩溃 RE、无限循环 TLE、内存超限 MLE、无限／有限大输出 OLE。
- [x] 验收通过：不可用镜像、Testcase 缺失／checksum 损坏及注入 Judge 异常均为安全 IE；隔离 TestClient 健康检查在 Worker 故障后仍为 200。
- [x] 验收通过：自动化验证不重复 claim／结果写入、Worker 独占和中断终结；真实 Docker verdict、编译失败及隔离用例后均无该 owner 容器或新增 job 目录残留。
- [x] 验收通过：真实容器内 fork 达到 PID 限制、外网连接失败、Docker socket／数据库／.env／root 私有路径不可取得，根文件系统不可写。

**范围边界：** Phase 2 的直接 Judge／Worker 证据不单独代表部署；Compose/Nginx、Custom Run 队列和独立 HTTP 客户端已在 Phase 3 另行隔离验收，但仍不代表正式环境部署。

## Phase 3：完整浏览器流程

**目标：** 用户无需脚本即可运行样例、自定义测试、提交和查看结果。

**依赖：** Phase 2；页面暴露规则需同步落实 Phase 5 的服务端策略。

**任务：**

- [x] 已有实现：C++20 textarea、输入框和 Submit；Custom Run 使用 Sandbox，不创建正式 Submission。
- [x] 已有实现：本人提交列表／详情、Admin 全部提交、源码、CE 日志、测试摘要与资源展示。
- [x] 本轮补齐：独立 Run Sample、Custom Test、Submit；样例可选择并在页面比较公开 expected，自定义输入不再伪装成样例入口。
- [x] 本轮决策并实现：保留同步 `POST /api/v1/runs`，内部新增短生命周期 CustomRun 数据库队列，由独占 Worker 执行；Server 不再需要 Docker CLI/socket，不新增公共轮询接口。排队等待超时或基础设施故障返回安全 503。
- [x] 本轮补齐：Custom Run 接受约定 `source_code`，兼容旧 `code`；两者同时出现但不同则 422。Web、测试和说明统一使用 `source_code`。
- [x] 本轮补齐：提交详情以现有查询接口自动更新并在终态刷新；请求失败指数退避，Run/Submit 显示 429 的 Retry-After、基础设施错误和输出截断提示。
- [x] 本轮补齐：Web 与 Agent Feedback 共用服务端转换；full、diagnostic、verdict_only 均作用于提交详情，非法模式启动即拒绝。生效模式现由 `/api/v1/me.feedback_mode` 告知；长度限制及最终白名单仍明确留在 Phase 5。
- [x] 本轮补齐：提供 systemd user unit，明确工作目录、`.env`、Conda 可执行文件、自动重启和 journal 日志；`systemd-analyze --user verify` 通过。Worker owner 标签可按实例配置，避免隔离实例互相清理。
- [x] 验收通过：Playwright Chromium 153.0.8010.12 真实执行登录、网页 Token、Run Sample、Custom Test、Submit 和自动结果刷新，无需把 TestClient 当作浏览器证据。
- [x] 验收通过：临时数据库／data／端口及独立 Compose project 下，以 Nginx + Server + 宿主 Worker 完成 `/minioj/` 全链路、CE、TLE、Docker 故障 503、Web health 和清理；同时修复代理非标准端口和静态资源 root_path 转发。

**交付物：** 完整 Web OJ、Custom Run 部署链路、状态更新、浏览器及部署验收记录。

**验收标准：**

- [x] 验收通过：登录 → 看题 → 输入代码 → Run Sample／Custom Test → Submit → 自动获取最终 AC。
- [x] 验收通过：Custom Run 仅接收调用方 stdin，不查询 hidden，数据库断言未创建额外 Submission；详情所有权回归和 Web／Agent 反馈策略一致性通过。
- [x] 验收通过（隔离等价重启）：Compose 服务从空临时环境启动，宿主 Worker 停止后以同一数据库／data 重新启动并完成 Docker 故障路径；未为此中断当前 WSL 或部署正式环境。

## Phase 4：Token 与普通机器接口

**目标：** 远程程序以 Bearer Token 和明确 JSON 契约使用 MiniOJ。

**依赖：** Phase 2 评测、Phase 3 Custom Run 和用户 Settings。

**任务：**

- [x] 已有实现：ApiToken、随机密钥与 SHA-256 摘要、一次展示、掩码 preview、Web 创建／列表／撤销。
- [x] 已有实现：Bearer、过期／revoked_at／禁用用户检查、last_used_at；Session 写请求使用 CSRF。
- [x] 已有实现：me、普通／Agent 题目、runs、submissions 创建查询、Token API；普通机器接口绑定显式请求／响应 Pydantic model 和 OpenAPI schema。
- [x] D10 决策并实现：保留 `DELETE /api/v1/tokens/{id}` 与 204，语义固定为幂等软撤销；保留 metadata 和 hash，`revoked_at` 生效后密钥立即无效。Web 保持原路径兼容并改称 Revoke，不提供公开硬删除。
- [x] 本轮补齐：API 错误以 `error.code/message/details` 为稳定结构，暂保留旧 `detail` 兼容字段；422 不回显原始输入。Submission 查询固定返回全部字段，非终态 verdict/tests/resources 及尚不可用的时间使用显式 null；started_at 在 Worker 领取后已有值，finished_at 在结束前为 null（RC 审计澄清文案，未改 HTTP）。`time_ms` 为毫秒，`memory_kb` 实际单位为 KiB，未知为 null。
- [x] 本轮补齐：源码、stdin 和 API testcase JSON 在解析前限制 Content-Length 与实际分块字节数；Web std／generator／testcase multipart 或表单在解析过程中限制总请求体及单文件大小，多字节值在解析后继续按 UTF-8 字节精确校验。
- [x] 验收通过：覆盖无效／过期／撤销／已删除 Token、禁用用户、跨用户资源、缺失身份、非法参数、资源不存在、Cookie CSRF 与 Bearer 免 CSRF；Token 原文不进入日志、数据库或 metadata 列表。
- [x] 验收通过：浏览器创建初始 Token；不 import MiniOJ 的独立 HTTP 客户端完成身份、公开／Agent 题目、Token 生命周期、Custom Run、提交、查询和 AC，分别覆盖直连根路径及 `/minioj` Nginx 子路径。

**交付物：** 普通 REST API、Token 生命周期、完整 schema 和错误表达、接口文档及测试记录。

**验收标准：**

- [x] 验收通过：Web 创建初始 Token，客户端凭 Bearer 完成普通机器流程，撤销密钥立即不可访问且 metadata 保留。
- [x] 验收通过：URL／参数不能突破权限；实际 JSON 经 response model 和 OpenAPI 回归约束，根路径／代理子路径行为一致。

## Phase 5：CodeHarness 协议与最终验收

**目标：** 独立远程客户端仅经 HTTP、Bearer 和 JSON 获取清洗题目、提交、轮询及结构化反馈。

**依赖：** Phase 4；联调前解决必要协议决策。CodeHarness 仅为远程客户端，本阶段不开发其内部逻辑。

**任务：**

- [x] 已有实现：程序用题目接口手工选择题面、说明、限制和样例，排除 rating、tags 和 hidden。
- [x] 已有实现：现有专用 Feedback API 保留原路径；授权且找到时所有状态 HTTP 200，显式 FeedbackResponse 固定核心字段。
- [x] 已有实现：AC/WA/CE/RE/TLE/MLE/OLE/IE 统一显式 schema；QUEUED/COMPILING/RUNNING 核心可空字段始终存在为 null。
- [x] 已有实现：生效模式由 `/api/v1/me.feedback_mode` 告知，配置沿用 `MINIOJ_FEEDBACK_POLICY`；不改变反馈内容权限。
- [x] 已有实现：diagnostic 仅安全诊断／CE 编译文本和 metadata；verdict_only 保留安全说明／失败序号，不包含编译／运行／testcase 内容。
- [x] 已有实现：普通提交 API、Feedback、Web 和 History 共用最终转换，从零投影 allowlist ∩ role；Admin／system 也不能绕过 diagnostic／verdict_only。
- [x] 已有实现：input/expected/actual/stderr／编译诊断上限 1024 UTF-8 字节、安全 Unicode 和截断 flags；去除内部路径、Docker／traceback／SQL／Secret，内部完整结果不缩减。
- [x] 验收通过：三模式 × 八种 verdict × owner/other/admin/system/匿名，pending／旧异常结果；最终 API JSON／HTML／History／错误体 sentinel，sample full 保持可用。
- [x] 已有实现：独立标准库 Reference Client，HTTP/Bearer/JSON 全流程、mode 验证、有限退避；可选 Idempotency-Key 及数据库级并发去重，丢失响应与轮询超时不重复创建。
- [x] 已有实现：题库 Web 50 题 SQL 分页／难度双向排序／字面量搜索，无 JS 可用；API 显式 page 才分页，保留数组兼容；边界／实际 HTTP 浏览器验收通过。
- [x] 已有实现：中英文 README、architecture、冻结契约及本轮命令／证据同步；未实现范围不冒充完成。
- [x] 已有实现：最小无 Secret CI、停止全部写入方的 SQLite 整目录＋全部 data 备份／新目录恢复工具；不删除 `.orig` 或既有数据。
- [x] 验收通过：发布审计本地全仓 **723 项 pytest**／静态检查，以及发布后 `40ede95` 的完整 hosted CI **725 passed**；Docker 八 verdict／backup → restore → AC、干净镜像独立 Nginx 和镜像字节核对保留为提交前快照，不外推为当前正式镜像全部源码一致。见顶部和验收记录。
- [x] 验收通过：GitHub 托管 CI 首跑问题已用最小独立 fix 修复，最新完整 [Checks run 37008639747](https://github.com/Sy-SU/MiniOJ/actions/runs/37008639747) 全绿；不代表正式升级／部署。
- [x] 验收通过：下列功能／安全／资源矩阵所列本地自动化与受控 Docker 范围；版本、步骤、结果与清理已记录，不代表正式上线。

**交付物：** 独立客户端协议、统一反馈策略、完整文档和最终验收记录。

**验收标准：**

- [x] 验收通过：独立客户端在根与 `/minioj/` 获取题目 → 提交 → 轮询 → Feedback；仅依赖 status/verdict，不解析 summary。
- [x] 验收通过：普通 API、Feedback 和页面按同一策略与角色边界处理；mode 由 me／Feedback 告知，客户端不能提高权限。
- [x] 验收通过：真实 Web、API、Worker、Docker 判题均在独立临时栈运行；MiniOJ 无需 CodeHarness 内部即可独立使用。

## 最终安全与资源验收矩阵

下表自动化范围已随发布审计本地 **723 项**及发布后 hosted CI **725 项**回归覆盖；八 verdict／backup→restore HTTP smoke、独立 Nginx／standalone Sandbox／Worker fault 的真实证据保留为提交前交付快照，不冒充发布后的 Docker 重跑。仅勾选所述范围，不等价于渗透测试认证、当前镜像全部字节一致或正式部署。正式升级仍待独立授权和验收。

| 范围 | 验收通过的实际证据 | 阶段 |
| --- | --- | --- |
| 密码、Session | [x] security/accounts/web/phase4 回归：hash、错误密码、登出、禁用身份和 Session/CSRF；隔离浏览器登录 | 1、5 |
| CSRF、XSS | [x] phase1/phase4/rendering/code_ui/management：错误 CSRF 被拒、危险 HTML 转义／诊断不回显 raw summary；新 full 检查包含既有浏览器 | 1、4、5 |
| Token、角色、所有权 | [x] tokens/management/phase5：一次展示、hash、过期／软撤销、other 404、role/CSRF、Token／Secret 不回显日志 | 1、4、5 |
| SQL 注入、路径穿越 | [x] problem_pagination 查询绑定／字面量转义及稳定 SQL；polygon/testlib/avatars/backup_restore 路径／symlink／范围回归 | 1、4、5 |
| 请求体大小 | [x] phase4/uploads：Content-Length／分块、多字节源码／stdin／multipart／双兼容字段总量，安全 413 | 1、4 |
| 时间、内存、输出 | [x] 当前 Docker judge 和 phase5：TLE/MLE/OLE、有限／无限输出、CE 编译超限；UTF-8 预览及 upstream flags | 2、3、5 |
| PID、网络、隔离 | [x] 当前 Docker sandbox：网络拒绝、PID 有界、socket／DB／.env／home 不可见、只读根；镜像无数据、Job／owner 清理 | 2、5 |
| hidden testcase | [x] phase5 最终 JSON/HTML/History body sentinel：ordinary/agent/problem/submission/feedback/error，8 verdict × 3 mode × 5 身份；sample full 可用 | 5 |
| 故障及清理 | [x] worker 并发 claim／终态／中断恢复自动化；真实坏／缺 testcase、不可用镜像为 IE，Nginx Custom Run 故障 503、health 200，临时 owner／Job／栈清理 | 2、3、5 |
| 部署、备份 | [x] 新干净镜像独立 Nginx 子路径／根 HTTP、标准库客户端；WAL＋题库／资产／头像 snapshot、只读校验／新目录 restore 后 AC | 3、5 |

## 已落实决策

- **D1／D2（Phase 5）：** Submission 原字段和 pending null 规则不变；Feedback 原 `/agent/.../feedback` 路径，授权且存在均 200，所有状态使用显式固定核心模型，八种 verdict 不从 summary 推断。沿用原错误包络／code 名，新增安全 500 分支。
- **D4（Phase 5）：** mode allowlist ∩ role；full 不提升普通用户 hidden 权限，diagnostic 安全 CE 文本＋metadata，verdict_only 可含失败序号但不含内容。所有暴露文本 1024 UTF-8 字节／truncated flags，诊断脱敏，内部存储不修改。
- **D5（Phase 5）：** 普通 POST 可选 Idempotency-Key，同 user/key/body 原 ID，不同 body 409；唯一索引和事务写锁，不依赖 API 进程 mutex。Reference Client 默认 60 秒轮询／15 秒 HTTP timeout 可配置，1/2/3/5/5… 退避；轮询超时不重提，POST 不确定时同 key/body 最多三次传输重试。
- **D3／D6（Phase 3）：** `POST /api/v1/runs` 保持同步成功响应，内部由数据库 CustomRun 队列和唯一 Worker 执行；请求接受 `source_code` 并兼容相同值的旧 `code`。正式提交成功仍为 202；正式提交和 Custom Run 排队容量独立配置，满载为 429 + `Retry-After`，基础设施不可用或 Custom Run 等待 Worker 超时为安全 503；不新增公共轮询路由。
- **D9（Phase 3 当前部署）：** 仓库 Compose 路径继续使用 `/minioj/`，Nginx 保留前缀、原始 Host 及非标准端口；数据库和 data 宿主挂载目录、HTTP 端口可为隔离部署覆盖。Server 不接 Docker，宿主 Worker 必须指向同一数据库／data；正式环境地址仍由部署者配置。
- **D8（按最新需求修订）：** 有提交也可编辑和删除。内部版本标记保留，但题目、提交及 Admin 均不显示修改／删除 warning；删除为软删除，仅打开不可用题目链接时提示“题目不存在”，历史成绩保留、题号不复用，不提供自动重判或完整历史题面恢复。删除时 QUEUED 提交结束为 IE，已读取数据的评测继续；generator 检查删除状态及 std checksum。Sample 精确关联及文件原子写入／回滚规则继续有效。
- **D7（Phase 2）：** V1 每个 Job 目录单 Worker 独占。中断任务在下次启动时终结为 IE／FAILED，不自动重跑；条件 claim 与条件终态写入避免重复领取和覆盖。
- **D11（Phase 1–2）：** 单个输入或生成输出默认上限 16 MiB；std／generator 源码沿用源码上限，单次运行默认 10 秒／512 MiB，单任务最多 50 例。保存实际输入／输出 SHA-256、完整 compile_result 及每个已执行用例的受限 metadata；首个 failure 内容继续由后续 Feedback Policy 控制。

## 协议决策状态

最新 Phase 5 附件授权后已落实下列决策；不再作为待明确的阻塞项。其它未来产品／赛制仍需独立确认。

| 编号 | 决策 | 实际状态 |
| --- | --- | --- |
| D1 | 固定核心字段／显式 null、非终态 Feedback 200 | 已实现；OpenAPI／全部 pending 验收通过 |
| D2 | 八种 verdict 同一反馈模型和既有错误包络 | 已实现；矩阵与真实 Docker HTTP 验收通过 |
| D4 | 白名单／角色交集、三模式／生效告知、1024 字节／flags | 已实现；sentinel／Unicode／脱敏验收通过 |
| D5 | 同 key 去重／冲突、并发唯一约束与有限轮询 | 已实现；并发／丢失响应／client timeout 验收通过 |
| D10（已落实） | DELETE 保持 204 并执行幂等软撤销，保留 metadata；不提供公开硬删除，现有 oj_ 前缀兼容 | Phase 4 |

## 原计划保留与范围调整

- 原 P0 的 Compose Custom Run、真实部署验收和 Worker 常驻配置并入 Phase 3。
- 原 P1 的恢复、统计、边界回归和容量并入 Phase 2；CI、备份和维护文档并入 Phase 5。
- Polygon 本地 ZIP 导入已按本轮明确要求纳入 Phase 1／3 扩展；内置 Codeforces 在线抓取、Brute/Stress Test、SSE、多 Worker／外部队列仍不纳入 V1 Core。仓库可选 `store/cf_import` 是另行授权的独立导题工具，不成为 Core 或 Reference Client 的依赖；用户题解／缓存／生成 evidence 不进入源码提交。C++ generator 保持 Phase 1 扩展范围。
- 原 Mac Coding Agent Harness 开发项移出 MiniOJ TODO。本仓库不含 Agent Loop、模型路由、LLM Provider、Prompt、Agent State、客户端 Workspace 或模型密钥开发任务；/agent/ 只保留为 HTTP 命名空间。

**后续起点：** Phase 0–5 与题库分页／排序已提交、推送并通过完整 hosted CI，当前为 MiniOJ V1 source RC complete；正式上线仍为 READY_WITH_NOTES，须单独授权 committed SHA 镜像构建、协调停写／一致性备份、迁移／部署／production smoke 和最终 tag。3 个旧历史已跟踪 `.orig` 的取消跟踪选择、原缺失 Polygon 截图包及 V1 范围外功能保持顶部列明状态，不擅自删除文件、改写历史或扩大任务。
