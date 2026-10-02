# Contest V1

V1 为公开比赛，有单场 Performance，无用户永久 rating、冻结榜或私密题库。比赛引用现有 Problem，按唯一顺序显示 A、B、…、Z、AA；不复制题目和 Judge。所有登录用户可报名，提交时自动报名；空报名也显示在排名中。

时间统一保存 UTC，管理表单明确使用 UTC。状态从当前时间计算：now < start 为 UPCOMING；start <= now < end 为 RUNNING；end <= now 为 ENDED。只有 RUNNING 允许比赛提交，服务端同时检查比赛、题目关联和非删除题目。普通提交接口和响应不增加必填参数；比赛使用独立创建入口调用同一提交服务，Submission 的 contest_id 可空。

比赛列表/详情/排名公开，UPCOMING 的比赛题目列表只对管理角色显示。题库原有题目仍公开，不宣称防止从 practice 提前看到题目。比赛开始后只禁止改时间；admin/system 通过既有内容管理权限可随时修改标题、说明、题目集合和顺序，包括已开始、已结束或已有提交的比赛，无额外确认／提交锁。删除整场比赛仍为软删除并保留提交/历史，RUNNING 期间拒绝删除。

管理表单输入现有题号后点 Add problem，通过 Remove／↑／↓ 调整，再 Save contest 原子应用；禁用 JavaScript 时可逐行编辑原题号框。服务端继续检查 CSRF、权限、题目存在且未删除、唯一题号、最多 100 题。移除仅删除 ContestProblem 关联，不删除 Problem、Submission 或 JudgeRun。题目列、solved、罚时和 Performance 始终只使用最新题目列表；添加题目的默认状态为未解，调整顺序不改变其结果，重新加入旧题时比赛时间窗内的历史有效 AC 重新计入。编辑与新提交继续共用比赛写锁；已接收／正在评测的提交不会被取消。

排名集中计算：solved 降序、penalty 升序，完全同分同罚时共享名次，以用户名稳定展示。每题按原提交时间和 id 排序，只统计比赛时间窗内、属于比赛题目、当前 FINISHED 的提交。第一条当前 AC 解题，罚时为从开赛到该提交的整分钟，加此前 WA/RE/TLE/MLE/OLE 每次 20 分钟；CE、IE、未完成提交不罚时。未解题不累计罚时，显示错误次数；AC 显示通过与此前错误次数。排名每次请求重新计算，Rejudge 后增减 solved/penalty 自动生效，不使用永久成绩缓存。

## 单场 Performance

后端纯函数 [contest_performance.py](../src/minioj/contest_performance.py) 接收当前比赛每题的 `(Problem.rating, solved)`，不依赖数据库。solved 与排名格子共用当前 FINISHED AC 状态，不使用 practice、评测历史 AC、未完成结果或提交次数。所有当前题目都参与，包括未解题；题目 rating 为 null 时用 1200。只为此估计将 rating 限定在 0–4000，不改题库中的原始值或题目编辑行为。

采用难度加权 logistic / Elo-like 模型：`p_i(R)=1/(1+10^((d_i-R)/400))`，`w_i=1+d_i/400`。最大化 `Σ w_i [y_i log p_i + (1-y_i) log(1-p_i)]`；导数零点等价于 `Σ w_i p_i(R)=Σ w_i y_i`。在 0–4000 上二分 60 次并四舍五入为整数。普通无权重、同斜率 Bernoulli MLE 的导数是 `Σ(y_i-p_i)`，仍然只看 AC 数量，无法满足“同数量 AC 难题更高”；本次权重明确保留具体解题难度。权重是本项目的可替换启发式，不声称是 Codeforces 官方 rating 或校准过的能力值。

边界：空题列表为 0；0 AC 为 `max(0,min(d)-400)`；全 AC 为 `min(4000,max(d)+400)`，避免无穷值。极端题目难度／搜索结果被限幅，限幅边界可能同分。题目 `[800,1200,1600,2000]` 按顺序 AC 0–4 题的结果见 TODO 实测。Performance 不参与排序或并列判定，也不写入 User，不增加永久 rating、delta/history 或颜色/title。刷新页面／JSON 即重算，修改题目列表、rating 或重判当前成绩无需失效缓存／迁移数据库。

## 路由和 JSON

网页：`/contests`、`/contests/{id}`、`/contests/{id}/problems/{label}`、`/contests/{id}/standings`；管理仍在 `/manage/contests`，题目列表修改复用原创建／编辑 POST，不新增重复写 API。比赛提交 POST `/api/v1/contests/{id}/submissions` 使用普通 SubmissionCreate 请求及 202 响应。Practice 与 Contest 的源码、结果访问仍遵守现有所有权和 Feedback Policy。

此前仅有 HTML 排名，本次新增公开只读 `GET /api/v1/contests/{id}/standings`，支持既有可选 Session／Bearer 身份，使用显式 ContestStandingsResponse 和原 Phase 4 错误包络。返回 `contest_id`、有序 `problems: [{problem_id,label}]`、`rows: [{rank,user_id,username,solved,penalty,performance,problems}]`；行内 problems 按 problem_id 映射 `{solved,wrong,minute,pending}`。不返回 email、源码、Judge 原始结果或隐藏测试。UPCOMING 的非管理访问按可见的空题目列表计算，行内 problems 为空、solved/penalty/performance 为 0，避免通过 Performance 推断尚未公开的比赛关联；admin/system 可见完整题目。不存在／已删除比赛为 404。既有共用 HTTP 接口、提交请求／响应、Token 和 Feedback Mode 不变。
