# MiniOJ 实施与验收 TODO

更新日期：2026-10-01。依据：本次提供的《MiniOJ：独立 OJ 项目规划与 Codex 执行提示词》。架构和协议见 [docs/architecture.md](docs/architecture.md)。

当前工作区已在保留原有未提交修改的前提下完成 Phase 0 基线复验、Phase 1 及其 std／generator 扩展；未推进 Phase 2–5，也未部署正式环境。真实 Docker 已复验 generator → std 链路，但没有重跑正式 Submission 的七种 verdict 冒烟。

## 状态与证据

- **[x] 已有实现**：已静态阅读代码，确认存在所述实现；不等于运行成功或整个阶段验收通过。
- **[x] 验收通过**：本轮已记录环境、命令和实际结果；只代表所述范围，不外推到其他阶段或正式部署。
- **[ ] 待实现／待补齐**：没有实现证据，或与规划存在差距。
- **[ ] 待验证**：实现或测试文件存在，但本轮没有运行；必须记录环境、版本、命令、结果和日期才能勾选验收项。
- **当前工作区实测（2026-10-01）**：Conda `minioj` 环境，Python 3.12.14、Ruff 0.16.9、pytest 8.4.2；编辑页改进后，`ruff format --check .`（51 个文件）、`ruff check .`、`pytest`（183 passed in 79.63s）、`node --check static/alerts.js`、`node --check static/problem_form.js` 和 `git diff --check` 均通过。隔离数据库及回环 Uvicorn 上的真实 Chromium 验证了警告鼠标／键盘关闭及刷新恢复、std 粘贴保存／上传回显、四个分区、桌面 1365px／手机 390px 无页面横向溢出，且无页面脚本错误；临时服务已停止。
- **此前验证**：Compose 配置解析、公式标记／本地资源回归与 KaTeX 0.18.10 Node 显示公式／MathML 冒烟通过；本次编辑页浏览器检查不代表所有公式视觉效果已验收。generator 扩展曾用现有 `minioj-cpp20:latest` 真实编译 generator 与 std，核对 2 组输入／输出；本轮只复验自动化回归，未重跑容器链路。Phase 0 时已确认真实 `.env` 被忽略且未跟踪。
- **隔离启动实测（2026-10-01）**：在 `/tmp` 临时目录连续两次执行 `minioj init-db`，确认 6 张业务表和独立 data/job 目录；以 Uvicorn 绑定 `127.0.0.1:18765` 后，`GET /`、`GET /healthz`、`GET /static/style.css` 均为 HTTP 200，随后正常停止并删除临时目录。

本轮开始时 HEAD 为 7940c7a；工作区已有 README.md、README_zh.md、restart、database.py、models.py、server/web.py、test_restart.py 的未提交修改，以及新增 tests/test_username_case.py。本轮保留并复验这些工作，没有回退或重建骨架；新增的 Phase 0 改动另见本节任务。

旧 TODO（2026-09-30）记录：基线 711bc3b 通过 Ruff 和 95 项 pytest；此前真实 Docker 冒烟覆盖七种 verdict 以及宿主机 TestClient → Worker → Docker → Feedback。保留为**历史验证记录**；本轮复验了当前工作区的 Docker-free 套件，但没有重跑真实 Docker 冒烟，也未核实远端推送状态。宿主机冒烟不等于 Compose 部署、全部安全边界或独立 HTTP 客户端已经验收。

| 范围 | 实现证据 | 自动化证据 |
| --- | --- | --- |
| 基础 | [pyproject.toml](pyproject.toml)、[config.py](src/minioj/config.py)、[database.py](src/minioj/database.py)、[入口](src/minioj/server/main.py)、[CLI](src/minioj/cli.py) | [配置测试](tests/test_config.py)、[测试入口](tests/conftest.py)、[升级测试](tests/test_database_upgrade.py)、[子路径测试](tests/test_subpath.py)；本轮均随 pytest 通过 |
| 用户、题目 | [模型](src/minioj/models.py)、[题目存储](src/minioj/problems.py)、[构建队列](src/minioj/testcase_builds.py)、[Markdown／TeX 解析](src/minioj/rendering.py)、[公式渲染](static/math.js)、[上传解析](src/minioj/server/uploads.py)、[Web](src/minioj/server/web.py) | [编辑／软删除／历史告警](tests/test_problem_lifecycle.py)、[Phase 1 回归](tests/test_phase1.py)、[std／generator](tests/test_testcase_builds.py)、[题目功能](tests/test_problem_features.py)、[升级测试](tests/test_database_upgrade.py) 等随 183 项 pytest 通过；编辑页 Chromium 检查通过；此前 KaTeX Node 冒烟通过 |
| Judge | [Worker](src/minioj/worker/main.py)、[DockerJudge](src/minioj/judge/runner.py)、[Checker](src/minioj/judge/checker.py)、[镜像](docker/cpp20/Dockerfile) | Judge／Docker 命令／Worker 自动化测试通过；[generator 真实冒烟](scripts/smoke_test_generator.py) 通过，[正式 Judge 冒烟](scripts/smoke_test_judge.py) 本轮未运行 |
| Web、Token、API | [API](src/minioj/server/api.py)、[schema](src/minioj/schemas.py)、[题目页](templates/problem_detail.html)、[提交页](templates/submission_detail.html)、[脚本](static/problem.js) | Web／Token／API 测试随 pytest 通过；[栈冒烟](scripts/smoke_test_stack.py) 本轮未运行 |
| 部署 | [Compose](compose.yaml)、[Nginx](deploy/nginx.conf)、[restart](restart)、[README](README.md)、[中文 README](README_zh.md) | [restart 测试](tests/test_restart.py) 与 Compose 配置解析通过；未运行真实 Compose 全流程 |

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
- [x] 验收通过：此前隔离 Uvicorn 的首页、健康检查、静态资源均返回 200，Compose 配置解析通过；当前工作区 Ruff、183 项 pytest 及差异检查通过。

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
- [x] 按用户新要求调整：已有提交仍允许编辑题目／std／Testcase；`Problem.revision` 与 `Submission.problem_revision` 用于旧提交 warning，不保存完整历史题面版本。整题采用 `deleted_at` 软删除，保留数据库行、测试文件和历史成绩，隐藏题目并拒绝后续提交／管理写入，题号不可复用；Sample 仍精确同步。
- [x] 已有实现：警告可用 × 关闭，刷新后恢复；std 支持直接粘贴、源码回显与独立保存，同时保留文件上传；编辑页增加四个编号分区和快捷导航，输入文件／generator 构建分别呈现。
- [x] 验收通过：覆盖普通用户伪造 API／Web 管理请求、禁用用户、提交所有权、路径穿越、跨题读取、符号链接，以及新增／更新／删除事务失败时 metadata 与文件一致性。
- [x] 验收通过：以唯一标记验证 hidden/generated 不经题目页、普通／Agent API、静态目录和未授权管理入口泄漏；题面 Markdown 的内嵌 HTML／危险 URL、样例、源码、编译输出、摘要和失败详情均有转义回归。公式解析和本地静态资源有自动化覆盖，KaTeX 运行时有 Node 冒烟；真实浏览器视觉效果仍归入浏览器验收。

**交付物：** 用户和题目模块、管理入口、可上传的测试数据存储、权限与一致性测试。

**验收标准：**

- [x] 验收通过：浏览器流程覆盖用户注册登录、Admin 建题、上传 std、由输入构建 sample/hidden、由 generator 构建 generated；普通用户只能看到公开样例，不能取得 std、generator 或 hidden。
- [x] 验收通过：修改 URL、伪造管理请求和存储路径不能越权；历史提交在题目编辑／删除后仍保留并提示变化，软删除失败整体回滚。
- [x] 验收通过：std 粘贴保存／回显、HTML 转义、Admin／CSRF／源码隐私、空白／超限／同时粘贴与上传拒绝及草稿保留均有自动化回归；真实 Chromium 验证警告鼠标／键盘关闭及刷新恢复、std 粘贴保存／文件上传回显，以及桌面和手机编辑页分区布局。
- [x] 验收通过：自动化覆盖 std／generator 队列、seed/index 参数、失败状态和原子批量保存；题目修改后旧排队提交不使用新数据评测，运行中评测使用已读取数据；删题取消构建、更换 std 拒绝过期任务，同一 std 连续追加任务可成功。此前真实 Docker 冒烟通过 2 组 generator → std 输入／输出，本轮不重跑容器编译链路。

**范围边界：** std／generator 的真实 Docker 最小链路已通过，但正式 Submission Judge、Worker 中断恢复、反向代理、外部 HTTP 客户端和正式部署仍未据此验收；这些仍属于 Phase 2–5。

## Phase 2：Submission 与 Judge

**目标：** 独立 Worker 真实评测 C++20，可靠保存结果、隔离执行和清理资源。

**依赖：** Phase 1 的用户、题目和 testcase。

**任务：**

- [x] 已有实现：Submission、202 入队、QUEUED 条件更新 claim、COMPILING/RUNNING/FINISHED 流程和独立 Worker。
- [x] 已有实现：DockerJudge 集中封装 Docker、容器内 g++ C++20 编译、逐用例执行和 Checker；正式请求只入队。
- [x] 已有实现：禁网、非 root、CPU／内存／PID／时间限制、只读根文件系统、任务目录挂载、输出监测及正常路径清理。
- [x] 已有实现：八种 verdict 分支、结构化结果存储、基础设施异常映射 IE；Checker 保留行内差异。
- [x] 已有实现：Judge mock、部分 Docker 命令／故障测试，以及七种 verdict 的真实 Docker 冒烟脚本。
- [ ] 待补齐：内部与 API 共用状态／verdict 枚举；当前为分散字符串，需校验非终态 verdict 和终态字段规则。
- [ ] 待补齐：CompileResult 保存 success、exit_code、stdout、stderr、time_ms 和截断信息；落实逐用例结果的保存范围。
- [ ] 待补齐：统一编译输出超限处理，覆盖短进程在采样前退出仍超限、stdout/stderr 合并限额和截断标记。
- [ ] 待补齐：区分用户返回 124/137、真实超时、OOM 与 Docker 故障；明确统计方法，采集失败不能当真实零占用。
- [ ] 待决策后实现：任务归属、中断恢复或终态、防重入；覆盖并发 claim、恢复和重复结果写入。
- [ ] 待补齐：处理 claim、读取题目和落库等外围异常；Worker 内部记录诊断，对外 IE 只返回安全类别与说明。
- [ ] 待补齐：清理失败可观测，覆盖异常／中断残留容器和目录，确认编译可写目录隔离。
- [ ] 待决策后实现：Custom Run 和正式提交的容量、并发及过载响应，维持 SQLite、单机 Worker 范围。
- [ ] 待补齐：提交创建、claim、编译／评测开始结束、verdict、Sandbox／Worker 异常日志；关联 job/submission，不记录 Secret。
- [ ] 待验证：专用环境中运行资源限制和故障注入，记录结果与清理证据。

**交付物：** 独立 Worker、C++20 镜像、完整评测路径、结果结构、恢复及受控测试记录。

**验收标准：**

- [ ] 待验证：正确程序 AC、错误答案 WA、编译错误 CE、用户崩溃 RE、无限循环 TLE、内存超限 MLE、无限输出 OLE。
- [ ] 待验证：Docker／Sandbox 失败、testcase 缺失损坏、Judge 异常均为 IE；Worker 故障不使 Web 崩溃或静默丢任务。
- [ ] 待验证：不重复 claim，中断任务最终恢复或终止，正常及异常结束都清理容器和目录。
- [ ] 待验证：受控 PID 超限、联网和宿主文件访问被限制；容器不可访问 socket、home、数据库、.env、其他题目和提交。

## Phase 3：完整浏览器流程

**目标：** 用户无需脚本即可运行样例、自定义测试、提交和查看结果。

**依赖：** Phase 2；页面暴露规则需同步落实 Phase 5 的服务端策略。

**任务：**

- [x] 已有实现：C++20 textarea、输入框、Run code 和 Submit；Custom Run 使用 Sandbox，不创建正式 Submission。
- [x] 已有实现：本人提交列表／详情、Admin 全部提交、源码、CE 日志、测试摘要与资源展示。
- [ ] 待补齐：独立 Run Sample、Custom Test、Submit 操作；当前只预填首个样例，没有独立 Run Sample。
- [ ] 待决策后修复：Custom Run 调度和同步／异步交付；Web 当前直接调用 Docker，Compose Server 缺 CLI 和连接通道。保留 POST /api/v1/runs，不擅加轮询接口。
- [ ] 待补齐：Custom Run 接收约定的 source_code，处理现有 code 兼容，并同步 Web、测试和说明。
- [ ] 待补齐：提交状态自动更新、请求失败及输出截断提示；当前提交详情需手动刷新。
- [ ] 待补齐：提交页应用统一 Feedback Mode；当前直接展示失败 input/expected/actual 和 CE 日志。
- [ ] 待补齐：WSL Worker 常驻配置，明确工作目录、环境、数据库／数据路径、Docker 依赖、自动重启和日志。
- [ ] 待验证：真实浏览器交互；已有 TestClient 页面渲染测试不能证明 JavaScript 和 Compose 流程可用。
- [ ] 待验证：隔离数据库、数据目录和端口，以 Nginx + Server + 宿主 Worker 验收 /minioj/ 下登录、Token、运行、提交、CE、TLE、Docker 故障及清理。

**交付物：** 完整 Web OJ、Custom Run 部署链路、状态更新、浏览器及部署验收记录。

**验收标准：**

- [ ] 待验证：登录 → 看题 → 输入代码 → Run Sample／Custom Test → Submit → 自动获取最终结果。
- [ ] 待验证：Custom Run 不读取 hidden 或创建正式提交；详情权限和反馈策略一致。
- [ ] 待验证：按说明从 WSL 重启后恢复 Web 和 Worker，Custom Run 与正式判题均可用。

## Phase 4：Token 与普通机器接口

**目标：** 远程程序以 Bearer Token 和明确 JSON 契约使用 MiniOJ。

**依赖：** Phase 2 评测、Phase 3 Custom Run 和用户 Settings。

**任务：**

- [x] 已有实现：ApiToken、随机密钥与 SHA-256 摘要、一次展示、掩码 preview、Web 创建／列表／删除。
- [x] 已有实现：Bearer、过期／revoked_at／禁用用户检查、last_used_at；Session 写请求使用 CSRF。
- [x] 已有实现：me、普通题目、runs、submissions 创建查询、Token API；请求 schema 和手工响应字段选择。
- [ ] 待决策后对齐：DELETE Token 当前硬删除，共用约定为撤销；明确 metadata 保留和兼容，不能称已实现软撤销。
- [ ] 待补齐：请求／响应 Pydantic schema、统一错误结构、未完成字段 null／省略规则及资源单位。
- [ ] 待补齐：源码、stdin、testcase 上传的解析前请求体限制；现有账号限制和解析后长度检查不足以覆盖全部入口。
- [ ] 待验证：无效／过期／撤销／删除 Token、跨用户资源、缺失身份、非法参数、资源不存在和 CSRF；日志无 Token 原文。
- [ ] 待验证：curl 或独立 HTTP 客户端完成身份、题目、Custom Run、提交、查询，覆盖根路径／代理子路径。

**交付物：** 普通 REST API、Token 生命周期、完整 schema 和错误表达、接口文档及测试记录。

**验收标准：**

- [ ] 待验证：Web 创建初始 Token，客户端凭 Bearer 完成普通机器流程，失效 Token 不可访问。
- [ ] 待验证：URL／参数不能突破权限，约定字段与实际 JSON 一致。

## Phase 5：CodeHarness 协议与最终验收

**目标：** 独立远程客户端仅经 HTTP、Bearer 和 JSON 获取清洗题目、提交、轮询及结构化反馈。

**依赖：** Phase 4；联调前解决必要协议决策。CodeHarness 仅为远程客户端，本阶段不开发其内部逻辑。

**任务：**

- [x] 已有实现：程序用题目接口手工选择题面、说明、限制和样例，排除 rating、tags 和 hidden。
- [x] 已有实现：专用 Feedback API、非终态响应、CE 编译信息及三种模式的局部过滤分支。
- [ ] 待补齐：AC/WA/CE/RE/TLE/MLE/OLE/IE 正式反馈 schema 和内部结果转换；当前是读取 judge_result 后局部删字段。
- [ ] 待决策后实现：diagnostic 白名单、verdict_only 受限说明／失败序号、生效模式告知。
- [ ] 待补齐：普通提交 API、Feedback API、Web／Admin 共用暴露规则；非法模式配置不能意外扩大暴露。
- [ ] 待补齐：限制 input、expected、actual、stderr、编译诊断长度，去除内部路径、Docker 日志和 traceback。
- [ ] 待验证：模式 × 全部 verdict × 本人／他人／Admin／匿名矩阵，覆盖 hidden/generated 泄漏。
- [ ] 待补齐：不 import MiniOJ、不共享数据库或文件的独立 HTTP 验收示例；现有栈冒烟直接 import 并调用 Worker，不能替代。
- [ ] 待补齐：统一中英文 README，覆盖定位、Ubuntu/WSL、配置、初始化、Admin、Web/Worker、镜像、建题／上传、三类运行、Token、普通／程序 API、模式、测试和最小流程；未实现标为计划。
- [ ] 待补齐：保留原计划中的 CI、SQLite + testcase 一致性备份恢复和维护说明；先核实 .orig 用途再决定清理，本轮不删除。
- [ ] 待验证：CI 静态检查／pytest 和隔离 Docker 验收；备份在独立目录恢复后完成一次判题。
- [ ] 待验证：完成下列功能、安全、资源验收矩阵，记录版本、环境、步骤、预期／实际结果和清理情况。

**交付物：** 独立客户端协议、统一反馈策略、完整文档和最终验收记录。

**验收标准：**

- [ ] 待验证：独立客户端获取题目 → 提交 → 轮询 → Feedback；根据 status/verdict 判断，不解析 summary。
- [ ] 待验证：普通 API、Feedback 和页面无策略旁路，客户端不能提高权限；full 和 verdict_only 不混作相同实验条件。
- [ ] 待验证：MiniOJ 无需 CodeHarness 即可独立使用，交付可运行 OJ 而非占位骨架。

## 最终安全与资源验收矩阵

以下均待验证；配置、实现和 mock 测试存在不代表受控运行通过。

| 范围 | 后续步骤／通过条件 | 阶段 |
| --- | --- | --- |
| 密码、Session | 仅存密码 hash；错误密码、登出、禁用账号及 Cookie 配置行为正确，登录失败有脱敏日志 | 1、5 |
| CSRF、XSS | Cookie 写请求拒绝错误 CSRF；题面、Token 名、源码和编译／运行输出按文本展示 | 1、4、5 |
| Token、角色、所有权 | 一次展示、hash、过期、撤销／删除；跨用户提交和管理请求拒绝，日志无 Secret | 1、4 |
| SQL 注入、路径穿越 | 恶意参数不改变查询含义；上传／读取／删除及符号链接不能越出允许目录 | 1、4 |
| 请求体大小 | 覆盖 Content-Length、分块和多字节源码／stdin／上传，解析前限制生效 | 1、4 |
| 时间、内存、输出 | 无限循环／内存超限／无限输出分别 TLE/MLE/OLE；输出缓冲和落盘增长受控，截断标记正确 | 2、3 |
| PID、网络、隔离 | 有界进程创建、联网、文件读取测试；socket、home、数据库、.env、其他题目／提交不可访问 | 2 |
| hidden testcase | 以唯一测试标记检查题目、提交、Feedback、Web、Admin 全出口，只在获准字段出现 | 5 |
| 故障及清理 | 停 Worker，注入 Docker／文件／数据库故障；无重复 claim、永久悬挂或资源残留 | 2、3 |
| 部署、备份 | HTTP 子路径全流程及 SQLite + testcase 一致性恢复可复现 | 3、5 |

## 已落实决策

- **D8（按新增需求修订）：** 有提交也可编辑和删除。修改增加版本标记，在题目及受影响旧提交上显示 warning；删除为软删除，旧链接显示删除提示、历史成绩保留，题号不复用，不提供自动重判或完整历史题面恢复。删除时 QUEUED 提交结束为 IE，已读取数据的评测继续；generator 检查删除状态及 std checksum。Sample 精确关联及文件原子写入／回滚规则继续有效。
- **D11（Phase 1 部分）：** 单个输入或生成输出默认上限 16 MiB；std／generator 源码沿用源码上限，单次运行默认 10 秒／512 MiB，单任务最多 50 例，均可配置。保存实际输入／输出 SHA-256；正式 Submission 的编译及逐用例结果保存仍属于 Phase 2。

## 待明确事项

架构文档说明现有行为；下表决策不能用当前硬编码值代替确认，不填未经确认的数字。

| 编号 | 决策 | 最晚落实 |
| --- | --- | --- |
| D1 | 提交完整 schema、未完成字段省略／null、非终态 Feedback HTTP 状态和字段 | Phase 4–5 联调前 |
| D2 | AC/RE/MLE/OLE/IE、通用 HTTP 错误完整 schema，以及 CE/WA/TLE 示例纳入契约 | Phase 4–5 联调前 |
| D3 | Custom Run 同步／异步、Worker/Sandbox 调度、失败状态、code 兼容及限制；不预设新轮询路由 | Phase 3–4 |
| D4 | Feedback 白名单、生效模式告知、权限关系、非法配置处理、内容截断 | Phase 3、5 |
| D5 | 请求超时、轮询间隔／总期限、提交超时后的去重／查重 | Phase 4–5 联调前 |
| D6 | 编译／运行／输入／输出／并发／队列具体上限，统计单位、跨 testcase 汇总、统计缺失表达 | Phase 2–4 |
| D7 | Worker claim 后异常退出的恢复／终态及防重入、重复写入 | Phase 2 |
| D9 | 正式根路径或代理子路径、外部 URL、持久化路径和 daemon 路径关系；Phase 0 已明确宿主／Compose 注入方式 | Phase 3 |
| D10 | Token 撤销／硬删除及 metadata 保留，现有 oj_ 前缀兼容 | Phase 4 |
| D11 | `MINIOJ_JOB_DIR` 与 testcase 上传边界已分别在 Phase 0、1 落实；编译／逐用例结果保存仍待确定 | Phase 2 |

## 原计划保留与范围调整

- 原 P0 的 Compose Custom Run、真实部署验收和 Worker 常驻配置并入 Phase 3。
- 原 P1 的恢复、统计、边界回归和容量并入 Phase 2；CI、备份和维护文档并入 Phase 5。
- 本地题目包／Codeforces 导入器、Brute/Stress Test、SSE、多 Worker／外部队列不纳入 V1 Phase 0–5，也不视为完成；C++ generator 已按本轮明确需求纳入 Phase 1 扩展。
- 原 Mac Coding Agent Harness 开发项移出 MiniOJ TODO。本仓库不含 Agent Loop、模型路由、LLM Provider、Prompt、Agent State、客户端 Workspace 或模型密钥开发任务；/agent/ 只保留为 HTTP 命名空间。

**后续起点：** Phase 0–1 已按上述范围验收。下一轮经明确指示后从 Phase 2 尚未完成项继续，不重建已有骨架；本轮不自动推进。若先恢复当前部署，仍需单独处理 Phase 3 Custom Run 阻塞及 Phase 5 反馈暴露缺口。各阶段仍须有各自完整验收证据。
