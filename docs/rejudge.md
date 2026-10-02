# Submission 与 JudgeRun

Submission 保持原 id、提交者、题目、源码、语言、创建时间及比赛关联。它保存当前评测结果；SubmissionJudgeRun 保存每一次 initial/rejudge 的触发者、触发时间、题目 revision、状态、结果和资源信息。

数据库升级为每个现存 Submission 回填一条 initial 历史，不执行重判，不改变原成绩。新提交在同一事务创建 initial 记录。重判前保留旧运行快照，再创建 rejudge 记录；Submission 的 generation 递增、revision 更新为当前题目、状态置 QUEUED，清空旧 verdict/结果/开始结束时间。

重判要求 admin/system 能力，Session POST 有 CSRF，FINISHED 以外返回 409；已删除题目返回 409，队列满返回 429。条件更新和事务保证同时请求只创建一次运行。Worker 沿用现有队列、DockerJudge、checker 和 sandbox；领取、运行、终态和中断恢复同步当前 JudgeRun，结果写入带 generation 条件，旧运行不得覆盖新结果。

当前运行中展示 QUEUED/COMPILING/RUNNING，当前 verdict 为 null。完成后当前结果是最新运行的终态 verdict（包括 IE，历史中保留此前正常结果），不会悄悄回退到旧 AC。个人 solved 和比赛排名只使用 FINISHED 的当前 verdict；重判中暂不计旧 AC。

管理入口：POST `/manage/submissions/{id}/rejudge`（确认后提交）；另提供 POST `/api/v1/manage/submissions/{id}/rejudge`，返回 202。GET 不修改状态。Judge History 的详情仍使用现有 Feedback Policy 与所有权/管理能力检查，不直接公开原始内部结果。V1 只实现单次重判，模型可供以后批量操作复用。

个人主页统计所有原始 practice/contest 提交，不把重判当作新提交。solved 使用当前 AC 的 DISTINCT problem；每题时间是这些当前 AC 中最早完成时间，旧提交没有 finished_at 时回退 created_at。某次旧 AC 被重判为 WA 后，若仍有其他当前 AC，该题仍 solved；最后一个当前 AC 失效才取消 solved。比赛罚时始终按原始提交时间，不使用重判完成时间。
