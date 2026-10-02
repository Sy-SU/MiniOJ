# Codeforces → MiniOJ 一次性导题工具

在仓库根目录、现有 `minioj` Python 环境中运行。无需安装 Agent SDK 或启动额外服务。所有生成代码复用 `minioj-cpp20:latest` 的 Docker 编译与运行沙箱，宿主必须能访问 Docker。标准程序、generator、validator 和 checker 的容器均不挂载配置、密钥、源码目录、数据库或 Docker socket，不继承宿主密钥环境变量。

## 配置与运行

复制 `config.example.toml` 为 `config.toml`，自行填写 Codeforces handle、LLM API 根地址和 model、MiniOJ 地址（保留 `/minioj` 等代理前缀）。公开示例不包含个人账号或凭证。`config.toml`、`secrets.env`、状态、缓存和生成题目均被 gitignore；只有工具代码、示例和文档进入版本控制。

密钥可通过环境变量提供，也可以直接填写本地 `store/cf_import/secrets.env`：

```dotenv
CF_IMPORT_LLM_API_KEY=你的模型密钥
CF_IMPORT_MINIOJ_TOKEN=你的MiniOJ管理员BearerToken
```

`secrets.env` 只解析配置中指定的密钥变量及字面值，不执行 shell；环境中已有非空变量优先。MiniOJ token 必须属于 `admin` 或 `system` 用户，可从已有 Settings 页创建。Codeforces 的公开 `user.status` 不需要 key；自定义兼容 API 可使用 `CF_STATUS_TOKEN`。LLM base_url 后会追加 `/chat/completions` 或 `/responses`，不要填写完整请求路径。DeepSeek 配置可直接使用 API 根地址 `https://api.deepseek.com`，模型名原样传递。`json_mode=false` 可兼容不接受 JSON mode 参数的提供商，客户端仍严格解析及验证 JSON。

先按[中文 README](../../README_zh.md) 的升级步骤协调停止写入方、备份并执行 `minioj init-db`，再启动本版 MiniOJ Server 和 Worker；这些是正式升级操作，须另行授权，不由导入器自动执行。已完成 MiniOJ V1 升级后，CF 导题工具不需要额外专属迁移。Server 提供管理员测试 checksum 查询，建题/编辑 API 支持 `lines`/`tokens`/`yesno`，以及 `checker=testlib` + `checker_source` 的独立 C++ checker，复用已有 checker bundle 和 Worker 沙箱。源码与题目原子保存；`GET /api/v1/admin/problems/{id}/checker` 仅返回类型、名称和 checksum，源码不公开。旧客户端编辑时省略 checker 会保留已有完整 checker；显式改为内置比较器会清除特殊 checker。升级时可用仓库已有 `./restart` 重建 Server。Nginx 的 testcase API 路径已单独匹配现有服务端请求体上限，避免正常较大测试被通用 2 MiB 限制拦截；升级配置需要 `nginx -t` 及 reload。其它路径仍使用原限制。

```bash
python -m store.cf_import scan
python -m store.cf_import sync
python -m store.cf_import import --dry-run --limit 3
python -m store.cf_import import --problem 1791C --dry-run
python -m store.cf_import import --problem 1791C
python -m store.cf_import import --limit 5
# 不设置 --limit 即处理所有本地已 AC 的候选题；已验证题自动跳过。
python -m store.cf_import import
```

`scan` 仅扫描和记录，不访问网络。`sync` 批量拉取完整提交历史并缓存 AC 集合。`--dry-run` 获取题面、调用 LLM、编译及生成验证数据，到测试就绪停止，完全不访问 MiniOJ。`--limit N` 限制本轮 AC 候选题目数（按 contest/index 排序，包含可能已导入的题）。指定未 AC 的本地题会跳过。首次导入建议逐题或用小批次。

```bash
python -m store.cf_import sync --refresh-ac
python -m store.cf_import import --refresh-ac --limit 3
python -m store.cf_import import --problem 1791C --force
python -m store.cf_import import --problem 1791C --retry-statements
python -m store.cf_import --config /tmp/my-import.toml scan
```

默认一直使用已有 AC 缓存，日志显示其抓取时间。账号新增 AC 后显式 `--refresh-ac`。`--force` 重建 spec、plan、代码和数据，并对同一远端题号重新验证；不会重复建题。它仅删除导入器保存了回执、且已不属于新集合的测试，保留手工上传的数据。题面缓存不会被 force 清除，避免重新请求网页。题面抓取失败会保存负缓存；只有 `--retry-statements` 才做一次新的尝试。

## 扫描和 AC 规则

支持实际目录形式 `store/codeforces/1791/C.cpp`、`C__Good.cpp`、`C__Fast.cpp`，以及 `1791C.cpp`、`1904_A.cpp`、`Codeforces_1791_C.cpp`。文件名和 contest 父目录冲突、未知变体、歧义编号、符号链接均记录 `unresolved`。`__Generator`、`__Checker`、`__Bad` 不作为标准程序。题号始终来自路径，LLM 不参与识别。

统一本地身份为 `codeforces:1791:C`；MiniOJ 默认题号 `CF1791C`，来源字段 `source=Codeforces`、`source_id=1791C`，复用现有 schema。远端已有相同来源身份时复用其题号；不同来源占用题号、来源身份重复或者软删除占用题号时拒绝覆盖。

账号 AC 只证明这道题曾通过，**不能证明某份本地文件就是提交过的 AC 代码**。本工具不会抓取提交源码或让 LLM 猜测。默认先尝试无 `__` 后缀的程序（例如 `C.cpp`），再尝试 `__Fast`，最后才尝试 `__Good`；同一类按路径排序，后缀大小写不影响优先级。`__Good` 是对拍辅助程序，名称不表示正确，也不保证能处理大数据；仅当前面的候选未通过编译／官方样例时才会尝试它。每份候选先编译并通过全部官方样例，选择结果保存为 `source_path/source_sha256`。可以在 `[references]` 中明确指定扫描到的某个候选文件。选择规则版本、按优先级排列的候选路径和源码 hash 都计入测试缓存；旧规则生成的数据或新增／修改候选不会因为已有 `verified` 状态而跳过重新选择和验证。对于有争议的本地变体，应核实选中的源码再批量运行；候选之间输出不一致时需要按题意独立验证，样例和自生成数据无法证明算法完全正确。

2026-10-02 已审计此前 129 道已导入题目的参考来源，其中只有 2092E、2107C 实际选择过 `__Good.cpp`；对这两题的全部现有测试进行隔离 Docker 重放和独立题意核验：

- 2107C：`C__Good.cpp` 在全已知数组分支只检查最后一个结尾状态，导致 6 组合法输入被错误输出为 No；正式解 `C.cpp` 通过全部 21,694 组。重新生成并导入了包含错误答案的两个测试文件，全部输入摘要不变；正式解验证提交 293 为 **27/27 AC**，此前误判的提交 291 已重判为 **27/27 AC**。
- 2092E：原 `E__Good.cpp` 的现有答案全部正确，但正式解 `E.cpp` 先把指数模 `mod`，在 20,047 组中有 20,013 组错误。已保留原文件换行并改为直接使用不超过 `10^18` 的指数；修正后通过全部独立核验，再以 `E.cpp` 重新导入并验证，提交 298 为 **20/20 AC**，全部原输入／输出摘要保持一致。

此次正常重跑复用已有 spec、plan、generator、validator、checker 和题面缓存，通过管理员 API 更新既有题号及导入器自有测试。新规则回归 `tests/test_cf_import.py` **37 passed**，覆盖优先级、大小写、显式覆盖恢复和旧缓存重新选择；Ruff lint／format 通过。本地完整审计记录为 `store/generated/reference-priority-audit-20261002.json`，修复前产物和状态备份在 `/tmp/minioj-reference-priority-20261002/`。上述独立答案核验覆盖两道使用 `__Good` 的题，129 的数字仅代表参考来源审计范围。

## 数据和验证

Codeforces 请求串行，API 间隔至少 2 秒；网页默认至少 3 秒，与 API 共用持久化请求时间戳。完整 `user.status` 按页读取、验证 author，只有完整成功才提交新的账号 AC 集合；未 AC 不获取 metadata/statement、不调用 LLM。Metadata 使用一次 `problemset.problems` 的 JSON 缓存，不遍历题库网页。题面只请求本地 AC 交集中的目标问题。

LLM 分别生成 `problem_spec.json`、`generator_plan.json`、`gen.cpp` 与独立 `validator.cpp`；每次 JSON 输出都经过 Pydantic schema 检查，非法结果最多重试 3 次，原始 response 保存在 `llm_responses/`。Plan 必须包含 small、boundary、random、structured、large、maximum 六类，可额外使用 adversarial 类，默认建议 10–30 文件，上限可配置。C++ generator 的参数是 `<test_id> <seed>`，一次只向 stdout 写一个完整输入；每个 test 用同一 seed 重跑两次并比对字节，拒绝 token 等价重复数据。

内置 token 结构检查支持整数范围、字符串长度/字符集、数组长度/范围/distinct/sorted/permutation、嵌套多组 testcase、sum 约束、无权图的顶点范围/simple/tree/connected/DAG。长度和范围表达式仅支持已读整数变量与 `+ - * // %`，不用 `eval`。复杂结构使用明确 warning 和独立 C++ validator 补充；LLM 生成的两个验证器仍可能共同误解题意，产物保留供检查，不能宣称形式化合法性证明。

所有样例、生成输入都经过 validator；reference 必须在题目原始 time/memory 限制下运行成功，输入和输出必须非空、UTF-8 合法、大小受限。generator/validator 默认 10 秒 CPU、512 MiB，单文件 8 MiB，总数据 64 MiB，可在配置调低。MiniOJ 的上传上限如小于 importer 配置，需要同步调低 importer；413 会留下明确失败状态。编译、运行、超时、输出超限、输入非法等任何失败均阻止上传；generator 最多修复一次（可配 0–2 次），修复额度跨重跑记录，手工修正产物或 force 可重新开始。

普通题使用 tokens，YES/NO 题使用 yesno，指定 lines 的题按行比较。**非唯一/构造答案和浮点容差题由 LLM 编写 `checker.cpp`**，从原始题面判断完整输出语义或明确的浮点容差。它是独立 C++20 程序，不依赖 testlib 头文件，复用 MiniOJ `testlib` 执行协议：`main <input_file> <contestant_output_file> <jury_answer_file>`，0 为 AC、1 为 WA、3 为 checker 内部错误。多答案题不能要求与 jury 输出逐 token 相等；需要最优值比较的题按题意比较最优值。交互题仍拒绝，因为它们需要 interactor。

另一次 LLM 调用在不提供 checker 实现的情况下生成 `checker_tests.json`：3–12 个微型合法输入、正确输出、带原因的错误输出和判定规则。多答案题必须至少有一个输入包含两份 token 序列不同的合法答案。checker 在独立 Docker 工作目录编译并运行，默认复用 Worker 的 10 秒 CPU、512 MiB 限制，先验证这些正反例、空输出、多余 token、官方样例和 reference，再检查所有生成测试的 reference 输出。任何异常、错误接受或错误拒绝都会阻止上传，保存诊断；checker 和 generator 分别使用 `llm.repair_attempts` 的修复额度，重跑不会无限重置额度。`checker_validation.json` 保存每次判定和源码/测试 checksum，恢复及远端跳过都会核对 checker checksum。LLM 正反例也可能误解题意，这些检查不构成正确性证明，产物可供人工检查。

此前因 `Unsupported non_unique checker` 失败的题，更新 Server 后直接重新运行即可从缓存继续，无需 `--force`：

```bash
python -m store.cf_import import --problem 2055C
```

## 恢复、去重和结果

```text
store/generated/codeforces/1791/C/
├── metadata.json
├── statement.html / statement.json / statement.md
├── problem_spec.json
├── generator_plan.json
├── gen.cpp / validator.cpp
├── checker.cpp / checker_tests.json / checker_validation.json  # 特殊判题题目
├── reference_candidates.json
├── tests_manifest.json
├── tests/01.in / 01.out / ...
├── llm_responses/ / logs/
├── minioj_verification.json
└── import_result.json
```

状态保存在 `store/cf_import/state.json`，用原子写入和独占文件锁防止两个导入进程同时修改。每道题记录状态、最后完成步骤、更新时间、错误、reference hash、MiniOJ 回执和 submission id。缓存产物存在时直接复用；生成数据用依赖 hash、每个文件 checksum、文件配对和限制重新检查。题面/metadata 发生变化则重建下游。修改源码、代码、spec 或 plan 后会使测试依赖失效并重新生成。

创建题目后每次测试上传按 `type + input SHA256 + output SHA256` 查询远端查重，即使服务器已提交但客户端没收到回执，重跑也不会追加相同测试。Server 不提供事务式整题上传；中断期间题目可能只有部分数据，下次运行继续补齐。所有上传完成后才提交本地 reference 并轮询。轮询超时保留 submission id，重跑先继续轮询已有未终态提交；已终态失败则重新提交验证。MiniOJ V1 API 已支持可选 `Idempotency-Key`，但本导入器尚未发送该头；创建 submission 的响应恰好丢失时，导入器仍可能额外产生一次验证提交，但不会重复创建题目/测试。该客户端能力缺口不改变共用 HTTP 契约，本轮发布审计不新增导入功能。

只有 MiniOJ 返回 `FINISHED + AC` 才记录 `status=verified, result=import_success`。其他 verdict 记录 `minioj_validation_failed`，题目和日志保留；CLI 返回非零。批次中某道题失败不会终止其它题，末尾 summary 包含本地代码、AC 交集、成功、未 AC、已导入、失败及 unresolved 数。`import_result.json` 可逐题检查。

LLM response、编译诊断和本地源码路径只保存在本地调试产物；HTTP 错误不回显 token、URL 查询参数或响应正文。原有题库、源码及其它修改不会被工具扫描结果覆盖。

## 验证工具

```bash
python -m pytest -q tests/test_cf_import.py
python -m scripts.smoke_test_cf_import
```

专项测试覆盖账号分页/过滤、缓存、statement 身份、两种 LLM API、JSON retry、非法输入、锁、dry-run、上传响应丢失/恢复、去重/force、非 AC verdict，以及 checker 原子保存、源码隐私、兼容旧编辑、大小校验、替代答案要求和独立修复额度。冒烟脚本在临时数据库和端口中运行真实 HTTP Server、Worker 和 Docker，从合成题面的上游 fixture 完成 12 文件生成、复现、验证、上传、AC 及重复跳过；另一个多答案 fixture 检验特殊 checker、不同合法构造的 Worker AC、错误构造的 WA，以及始终接受/始终拒绝/内部错误 checker 的导入拒绝。还实测网络/密钥/文件隔离、超时、输出限制与容器清理。它不调用付费 LLM、不替代真实 Codeforces 题目质量验收，也不写入个人 MiniOJ 实例。

2026-10-01 checker 扩展实测：相关 importer/checker/API/lifecycle/Phase 4 回归 **135 passed**；上述真实 Docker 冒烟通过。2055C 使用配置中的 DeepSeek 实际生成 checker、独立例子和 15 个测试文件，checker 的 70 次正反例/样例/reference 判定全部通过。独立 Python 验证全部 21,280 组、5,432,332 个格子，以及 9 个合法输出、29 个非法输出例子。随后在临时 HTTP Server、Worker、Docker 中，原始本地代码与另一份使用不同公共和的合法构造均 **16/16 AC**，错误网格 **WA**，重复导入跳过。记录位于 `store/generated/codeforces/2055/C/{checker_validation,independent_validation,isolated_minioj_verification}.json`；这次临时验收没有更新个人 MiniOJ 服务或导入该题，正式使用须先加载新版 Server API。

官方接口依据：[Codeforces API](https://codeforces.com/apiHelp)、[user.status / problemset.problems](https://codeforces.com/apiHelp/methods)、[OpenAI JSON 输出格式](https://developers.openai.com/api/docs/guides/structured-outputs)。
