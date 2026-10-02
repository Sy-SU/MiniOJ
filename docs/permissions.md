# MiniOJ 权限矩阵

角色为 `user`、`admin`、`system`，通过集中能力策略判断，不比较字符串大小。system 包含 admin 的内容管理能力。

| 功能 | user | admin | system |
| --- | --- | --- | --- |
| 浏览题目、提交代码、Custom Run | ✓ | ✓ | ✓ |
| 参加公开比赛、查看排名 | ✓ | ✓ | ✓ |
| 管理自己的 Profile、Avatar、Password、Token | ✓ | ✓ | ✓ |
| 创建、编辑、软删除题目及管理 testcase | × | ✓ | ✓ |
| 查看全部 Submission、代码及受策略限制的结果 | × | ✓ | ✓ |
| 单 Submission Rejudge | × | ✓ | ✓ |
| 创建、编辑、软删除比赛及管理题目顺序 | × | ✓ | ✓ |
| 管理用户、修改角色、启用/禁用用户 | × | × | ✓ |
| 查看系统状态 | × | × | ✓ |
| 系统配置操作 | × | × | ✓ |

后端所有管理路由校验能力，导航可见性仅用于体验。Session 写操作要求 CSRF；Bearer 管理 API 使用相同角色策略。未知角色没有管理能力。

升级时 `init-db` 在事务中执行一次 `roles-v2` 迁移，将当时所有旧 `admin` 改为 `system`，记录迁移标记。再次启动不提升迁移后创建的新 admin。原 `create-admin` 命令及 `MINIOJ_ADMIN_*` 引导配置保留兼容，创建/验证 system 引导账户。

只有 system 能调整角色；禁止自身降级或禁用，禁止降级/禁用最后一个活跃 system。V1 不提供删除用户接口或在线修改环境配置。System 页仅展示无 secret 的状态与计数。

已有安装必须先停止旧 Server 与 Worker，备份同一份数据库和 data，再安装新版并执行 `minioj init-db`；核对原管理员已变为 system 后才启动新进程。不要在新库升级后混跑旧角色代码，不要删除迁移标记。具体操作见 [中文 README](../README_zh.md#已有安装升级三角色比赛与重判)／[English README](../README.md#upgrading-an-existing-installation).
