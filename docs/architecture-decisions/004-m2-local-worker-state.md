# ADR-004：M2 本地 worker 生命周期与 SQLite 状态

- 日期：2026-11-17
- 状态：本地可靠性实现接受；Docker 安全隔离和远程适配未验收
- 范围：单机 Linux/WSL、受信任 mock、无真实模型调用

## 调度与底座边界

继续复用 Inspect 的单样本执行、限制与原生日志；不叠加 `eval_set` 的自动重试。本地控制器只负责外部进程生命周期、执行身份、产物冻结和状态持久化。原生 Eval Sets/Flow 的批次复用能力不能直接作为“外部进程已经停止”的证据；本轮以父进程崩溃、孤儿 worker 和遗留子进程的可执行测试验证该边界。

Flow 未做完整可执行比较，因此不宣称它无法满足后续 BB 接入。进入 M3 时仍应评估其公开扩展点；本实现不扩展成通用分布式调度器。此记录细化 [ADR-001](<001-inspect-boundary.md>) 的 M2 本地部分。

## 持久化与启动协议

一个实验只有一个驱动控制器持有 Linux `flock`，进程死亡自动释放，worker 不继承该锁。`BEGIN IMMEDIATE` 事务同时 claim queued run、创建 attempt、幂等键和事件；任何未结束 attempt 都阻止新派发。

worker 首先等待父进程管道放行。控制器启动新 session 后，保存 PID、启动 tick、boot ID、owner、deadline 和资源记录，事务成功后才发送放行字节。登记前控制器死亡会关闭管道，worker 不执行任务；登记后死亡由新控制器接管同一个 attempt。

worker 接收已冻结的公共任务和 staged 输入，不加载原始配置、GradingSpec 或模型凭据。worker 只生成 `.eval` 和 outcome；控制器等待整个记录的进程组退出后，发布快照、结果并提交 SQLite。schema v1 可迁移至 v2；旧活跃 worker 没有身份信息时停止自动恢复，要求人工核对。

## 恢复与重试

- `pause` 停止新派发，当前样本允许正常结束；`resume` 解除暂停并继续 queued run。
- 新控制器发现原 worker 仍存活时，接管并继续观测，不再启动同一任务。
- 已退出但 outcome 完整，或快照/结果发布后控制器崩溃：校验身份和哈希，收集同一 attempt。
- 没有 outcome：保留部分产物，记 `infra_error`。`infrastructure_retries=0`，不会创建新 attempt 自动重试或美化成绩。
- 重复 resume 不更改旧结果、不新增执行或相同自动评分；显式 `score` 可以追加新批次。
- 原始执行索引按内容哈希追加存档，报告固定其使用的版本；当前索引可原子更新，历史报告不受影响。

## 停止与资源核验

`cancel` 首先持久化取消意图；queued run 记 cancelled/not_applicable，不捏造交付或能力失败。活跃控制器观测该意图；若控制器死亡，cancel 可取得锁后直接接管清理，不依赖原评分材料。

超时和取消先记录 stop reason，再向核验过的进程组发送 TERM/KILL。leader 先退出时仍检查同组子进程；PID 被复用或清理无法确认时标 unknown/recovery_required，阻止派发，拒绝误杀无关进程。取消请求被接受与实际 cancelled 状态分开显示。

## 验证与边界

本轮默认完整回归：**54 passed，1 skipped**。涵盖 SQLite 重开/迁移、事件、并发 claim、owner fencing、两个控制器互斥、门控管道关闭、暂停恢复、配置变化拒绝、启动失败、孤儿 worker、deadline、取消后清理、leader 退出后子进程、PID 身份变化、结果发布前后崩溃、报告证据固定及凭据未传递。

Docker 探针默认 skip；此前显式探针因宿主 Engine 20.10.20 低于要求 24.0.6 而失败，本轮没有升级或绕过。私有数据不可达、容器销毁前导出和不可信代码安全隔离尚未验收。进程组机制不控制主动脱离 session 的恶意进程，不能当作安全沙箱。跨主机、BB、真实预算及提供方在途调用回收属于后续范围。
