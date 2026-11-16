# Agent Testbed 详细开发方案

- **日期**：2026-11-16
- **文档状态**：开发设计草案；本文不是实现完成记录，也不是付费运行授权。
- **适用仓库**：`agent-testbed`；编写时基线为 `8baaff1`，仓库已有说明、许可证、协作规范和架构图，尚无应用代码与测试工程。
- **上位设计**：[原始建设计划](</home/hjdssj/project/work/feysh-bb/output/agent-testbed-plan.md>)。本文将其细化为接口、数据结构、工作包、测试和验收门槛，不修改原始计划；该链接指向本机外部资料，不是仓库内依赖。
- **现有说明**：[项目定位](<../README.md>)、[协作与提交规范](<../agent.md>)。
- **证据边界**：Inspect 能力依据本次查阅的官方文档，来源编号见第 19 节；BB API、依赖版本和运行环境尚未做可执行验证，不视为已兼容。

> 交付目标：让不同 Agent 系统完成同一批任务，用统一且独立的规则验收，并把质量、成本、耗时、失败和资源回收情况连成可追溯的实验记录。
>
> 实施原则：先完成不调用真实模型的闭环，再接真实系统，最后经明确授权执行小规模付费试跑。不是重新开发一套 Agent 框架，也不是先开发评测 Web 平台。

## 目录

1. 范围、成功标准与关键决策
2. Inspect 复用边界与兼容性验证
3. 总体架构与模块职责
4. 核心数据契约
5. 生命周期、取消与恢复
6. Inspect Backend 与系统适配器
7. 首批任务包和评分设计
8. 用量、预算与模型接入
9. 数据存储与产物管理
10. CLI 和配置契约
11. 实验设计与报告
12. 安全、隔离与权限
13. 工程结构、依赖和持续集成
14. 测试矩阵
15. 分阶段工作包与验收
16. 第一批可执行开发任务
17. 风险、决策门槛与待确认事项
18. 第一版发布检查表
19. 参考资料与验证说明

## 1. 范围、成功标准与关键决策

### 1.1 第一版到底交付什么

第一版交付一个可安装的 Python 包和 `agent-eval` CLI，支持以下闭环：

```text
验证配置 → 展开运行矩阵 → 启动被测系统 → 收集并冻结产物
        → 独立评分 → 汇总对比 → 从报告追溯原始证据
```

同时具备暂停派发、取消、恢复、预算拒绝和资源清理能力。最终演示为 **5 道固定题 × 4 个系统 × 1 次重复 = 20 个逻辑运行**，而不是只展示一次成功回答。

四个系统：

| 系统 ID | 被测系统 | 第一版定位 |
|---|---|---|
| `single-agent` | 单 Agent，同等外部工具能力 | 基线 A |
| `planner-worker` | 固定策略的基础规划者—执行者 | 基线 B |
| `bb-commander` | BB commander 模式 | 方案 C |
| `bb-concerto` | BB concerto 模式 | 方案 D |

首轮可先接 A、C、D 进行工程联调；正式四组试跑必须补齐 B。若某组只能黑盒接入，允许报告其质量与耗时，但不得把它写入“严格同预算”结论。

### 1.2 第一版明确不做

- 不重写被测系统的规划、协作、记忆或工具循环。
- 不同时维护 Inspect 与 Harbor 两套执行调度核心。
- 不做多租户、复杂权限门户、集群调度或新的常驻 Web 服务。
- 不一次导入多个公开 Benchmark，不以排行榜覆盖数量作为完成标准。
- 不使用公司真实业务资料作为默认 fixture；先采用合成、可授权、可复现材料。
- 不把示例配置中的模型、并发和 token 上限视为用户授权。
- 不通过隐藏测试反馈反向提示 Agent，也不因答错而自动重试到答对。

### 1.3 本方案采用的关键决策

| 编号 | 决策 | 原因与边界 |
|---|---|---|
| D01 | Python 控制面，优先复用 Inspect | 保留任务执行、日志和评分扩展能力，不从零造评测引擎 |
| D02 | 控制器只管理实验矩阵和外部生命周期 | 与 Inspect 的样本执行职责分开，避免双重重试与双重恢复 |
| D03 | 第一版一个 attempt 对应一个单样本 Inspect 执行单元 | 优先保证归属、取消、日志和恢复清晰；后续再优化批处理开销 |
| D04 | 公共任务与私有评分材料分开加载 | 防止目标答案、隐藏测试和评分凭据进入 Agent 工作区 |
| D05 | 稳定逻辑运行 ID + 独立 attempt ID | 区分重复实验、基础设施重试和中断恢复 |
| D06 | SQLite 保存状态与账本，文件保存大对象 | 先满足单机持久化和审计，不先建设分布式存储 |
| D07 | 先确定性评分，模型评审可选且单独授权 | 第一阶段无模型费用，评分可调试、可回放 |
| D08 | 接入等级与预算保证分别声明 | `controlled` 不自动等于精确硬上限，必须有相应证据 |
| D09 | 原始执行与评分版本不覆盖 | 可离线重评分、审查失败尝试和复核报告 |
| D10 | 第一版默认样本级并发为 1 | 降低资源争用及恢复复杂性；系统内部并发仍需记录 |

D02/D03 描述本方案的默认实现路径；M0 还需按第 2.3 节比较官方配套编排能力。无论最终复用还是自建，都保留单一调度权威、稳定运行身份和完整审计这三个约束。

## 2. Inspect 复用边界与兼容性验证

### 2.1 已查阅的官方能力与项目选择

以下“官方能力”仅指文档明确描述的能力，不表示当前仓库已经安装或测试。

| 方面 | 官方能力 | 本项目采用方式 | 仍需我们负责 |
|---|---|---|---|
| 任务定义 | `Task` 组合 dataset、solver、scorer；`Sample` 提供输入和文件等字段 [S01][S02] | 将公共 `TaskSpec` 转为单样本执行任务 | 实验矩阵、版本、权限和跨系统一致性 |
| 外部 Agent | Bridge 将同进程 Python 或沙箱内 Agent 的模型调用转入 Inspect [S03] | 兼容性验证后用于可控入口 | BB 远程调用归属、端点覆盖、取消与资源确认 |
| 限制 | 提供样本时间、token 等限制；token 检查与模型生成调用有关 [S04] | 作为执行层保险，映射停止原因 | 跨进程原子预算、在途请求、实验总上限 |
| 评分 | 支持自定义评分、延迟评分及已有日志重评分 [S05][S06] | 复用评分接口；私有资料仅在评分阶段加载 | 任务评分规则、产物快照、评分版本和授权 |
| 批量运行 | Eval Sets 提供恢复、重试与样本复用 [S07] | 先不与自有运行账本叠加调度；后续专门评估批量模式 | 远端运行幂等、逻辑 run/attempt 的权威记录 |
| 沙箱 | 内置 Docker 支持，可扩展其他环境 [S08] | 用于工具执行和文件/代码任务 | 权限、网络策略、远程服务隔离和资源回收 |
| 证据查看 | 原生日志和 Inspect View [S09][S10] | 保留原生日志，用查看器调试 | 跨架构汇总、数据缺口和实验级报告 |

**关键区别：** 官方文档明确指出，设置沙箱不代表所有 Python 工具、Agent、评分器代码都自动在容器内执行；需要通过沙箱接口发起对应工作 [S08]。因此本项目不能把“配置了 Docker”当成隐藏答案已经安全隔离。

**远程接入限制：** Sandbox Bridge 的代理运行于沙箱内，并非可以直接给任意远程 BB 服务使用的公开模型网关 [S03]。BB 的实际接入方式需要专项验证，不能只替换一个 URL 就宣布完成。

### 2.2 M0 必须完成的六项 spike

| 验证项 | 操作 | 必须保留的证据 | 未通过时的处理 |
|---|---|---|---|
| SP01 基础执行 | 固定版本下运行一个不生成模型请求的 solver | 依赖锁、命令、原始日志、解析结果 | 先修兼容问题，不开始业务适配 |
| SP02 文件与快照 | 在沙箱创建文件，在销毁前导出并校验哈希 | 容器清理前后产物一致性测试 | 明确收集钩子，不能靠延迟清理碰运气 |
| SP03 停止语义 | 模拟耗时任务、限制触发和外部取消 | stop reason、终止确认、遗留资源检查 | 由外层监管补足，能力声明降级 |
| SP04 Bridge 覆盖 | 用本地假模型服务验证所有预期角色调用 | 请求 ID、归属、用量、遗漏检测 | 不支持的协议或角色阻止受控接入 |
| SP05 离线评分 | 执行后更换评分版本，不再启动 Agent | 新旧评分记录、零新增执行请求 | 修正日志/快照重建接口 |
| SP06 日志和恢复 | 中断执行子进程后检查可恢复信息 | 可关联的 run、attempt、Inspect log ID | 无法继续时显式终止，不偷偷新建工作 |

依赖版本在 SP01 通过后写入锁文件及兼容性记录。本文不编造“已验证的 Inspect 最新版本”，也不将在线文档中存在的 API 自动视为所有版本都具备。

### 2.3 先排除重复建设，再确定控制层实现

官方 Eval Sets 文档还介绍了配套 **Inspect Flow**：支持声明式实验、参数矩阵、默认配置复用、日志索引/复用及后处理 [S07]。这与部分拟建功能重叠，M0 必须比较“原生 Eval Sets/Flow + 薄适配”与本文“最小自有 Controller”两种路径，不能未经验证就重写成熟能力。

决策依据是远程 BB 幂等恢复、跨角色准入预算、资源终止确认和不可变审计是否能通过公开扩展接口满足。能复用时优先复用，本方案的契约和验收标准不变，自有代码缩减为必要桥接；无法满足时，才按第 3～6 节实现最小 Controller。M0 的 ADR 需记录具体缺口、复用模块和唯一调度/重试所有者。第一版不叠加三个都能重试的调度层，也不先实现一个通用分布式调度器。

## 3. 总体架构与模块职责

### 3.1 数据流与信任边界

```text
                 可信控制面（Agent 不可读取）
CLI / 配置 ──→ ExperimentController ──→ SQLite 状态 / 预算账本
                     │
                     │ 一个 attempt，一个执行句柄
                     ▼
               InspectBackend
                     │
          ┌──────────┼───────────────┐
          ▼          ▼               ▼
     PythonAdapter CLIAdapter     BBHttpAdapter
          │          │               │
          └────── 被测系统 / 独立任务环境 ──────┘
                       仅公共题目、输入、公开验收要求
                     │
             原始轨迹 / 回答 / 文件 / 环境变化
                     ▼
              ArtifactSnapshotter
                     │ 不可变 snapshot + 哈希
                     ▼
       GradingService + 私有 GradingSpec / 隐藏测试
                     │
                     ▼
             ScoreResult → ReportBuilder

所有真实模型调用：运行身份 → 已授权模型入口 → 预算准入 → 提供方
所有重试 / 取消 / 在途消耗：仍归属原逻辑 run 和对应 attempt
```

### 3.2 模块职责

| 模块 | 负责 | 不负责 |
|---|---|---|
| `contracts` | 版本化 schema、枚举、序列化和校验 | 调用 BB、访问模型 |
| `config` | 解析 YAML、解析引用、固定配置和权限检查 | 隐式安装依赖、发起付费请求 |
| `controller` | 运行矩阵、队列、状态转换、租约、恢复和取消编排 | 重写 Agent 推理循环 |
| `backends/inspect` | 公共任务转换、Inspect 调用、结果与日志归一化 | 判定实验优劣、持有私有答案 |
| `adapters` | 系统启动/查询/取消/收集/清理/能力声明 | 填写外部得分 |
| `environments` | 公共输入 staging、沙箱、凭据最小化、工作区 | 共享全局记忆、分发评分数据 |
| `metering` | 请求归属、预算预留与结算、用量完整性 | 无证据补零、自动改模型或放宽限额 |
| `storage` | 数据库迁移、事务、事件、产物索引 | 大文件塞入状态表 |
| `grading` | 读取冻结结果、确定性/可选模型评分 | 让被测 Agent 修改判分规则 |
| `reports` | 对比、缺失披露、CSV/JSON/HTML/Markdown 导出 | 删除失败样本、生成虚构结论 |

接口通过不可变数据对象和显式句柄连接；核心不能导入 BB 的内部类型。第一版采用显式 adapter 注册表，不开发复杂插件分发系统。

## 4. 核心数据契约

### 4.1 统一身份与版本

- `experiment_id`：一次实验的永久身份；重新执行相同配置也产生新的实验身份。
- `run_id`：实验内 `task_revision × system_revision × repeat_index` 的逻辑身份，创建后不变。
- `attempt_id`：一次实际启动尝试的身份；恢复已有尝试不产生新 ID，允许的基础设施重试才新建。
- `request_id`：网关准入层产生的请求身份；提供方请求 ID 另存，不能直接依赖其全局唯一。
- `snapshot_id`：一次冻结产物的身份，附内容清单及 SHA-256。
- `score_id`：某快照在特定 grader 版本下的一次评分身份。
- 所有对象带 `schema_version`；时间统一存 UTC，报告可按指定时区显示。
- 配置、输入、提示词、系统代码、评分器、模型标识、镜像 digest 和锁文件均记录版本或哈希。

`run_id` 不因基础设施重试变化；`repeat_index` 是预定重复，不是“再试一次直到成功”。配置变化须新建实验，禁止复用旧 ID 静默改预算。

### 4.2 对象字段

| 对象 | 必需字段/概念 | 可见范围 |
|---|---|---|
| `TaskSpec` | schema、task ID/revision、instruction、public input manifest、输出 schema、公开验收要求、所需工具和资源、网络策略 | Agent 可见 |
| `GradingSpec` | task revision、grader ID/version、private asset refs、判定标准、权重、容差、评分超时 | 仅评分侧 |
| `SystemSpec` | system ID/revision、adapter kind、模式、角色—模型映射、提示词哈希、能力声明、环境 profile | 控制侧；仅必要配置进入 Agent |
| `RunConfig` | experiment/run/attempt ID、重复编号、种子、执行及评分上限、deadline、工具权限、预算模式 | 控制侧；可公开的限额可告知 Agent |
| `RunHandle` | adapter ID、attempt ID、remote ID 或进程/容器标识、幂等键、owner token、可重连信息 | 控制侧持久化 |
| `RunResult` | terminal reason、回答、artifact refs、trace refs、用量摘要、自报状态、部分产物标记、时间戳 | 评分和报告侧 |
| `ScoreResult` | score ID、snapshot ID、grader revision、status、各维度分、pass 判定、证据、评分消耗 | 仅评分侧生成 |
| `CleanupResult` | resource ID 列表、stop confirmation、清理状态、失败原因、最后检查时间 | 控制和报告侧 |

所有“未知”字段使用 `null + missing_reason`，不能填造一个看似合理的数值。错误文本与产物先视为不可信数据，不能直接插入 shell、HTML 或日志命令。

### 4.3 能力声明

最低需表达：

```yaml
schema_version: "1"
capabilities:
  start_idempotency: native        # native | lookup | none
  reconnect: true
  cancel_request: true
  termination_confirmation: true
  collect_partial: true
  artifact_snapshot: true
  trace_level: model_calls         # none | summary | model_calls | full
  usage_level: per_call            # none | aggregate | per_call
  model_endpoint_override: true
  model_admission_control: true
  workspace_isolation: container   # process | container | dedicated_service
  cross_run_memory_reset: verified # verified | declared | unknown
```

这是项目自定义协议示例，不是已存在的 BB 返回值。`validate` 将能力与任务/实验条件匹配；声明本身不是证明，需要关联对应的适配器契约测试结果。

### 4.4 三个独立维度

- `integration_level = black_box | controlled`：可观测和可控能力。
- `budget_enforcement = observed | admission_controlled | hard_bound_verified`：预算保证等级。
- `usage_completeness = complete | partial | unknown`：记录完整性。

`hard_bound_verified` 只有在输入上界、输出上界、重试路径和全部角色调用均可验证时允许标注；否则即使设置准入，也只能写 `admission_controlled` 并披露误差。

## 5. 生命周期、取消与恢复

### 5.1 避免“一个状态字段表达所有事情”

持久化以下独立字段：

```text
execution_phase:
  queued → preparing → starting → running → stopping → collecting → terminal

terminal_reason:
  finished | cancelled | timed_out | budget_exceeded |
  infra_error | agent_error | blocked | recovery_required

grading_status:
  unscored | scoring | passed | failed | grader_error

cleanup_status:
  pending | in_progress | confirmed | failed | unknown
```

正常完成允许由 `running` 直接进入 `collecting`；尚未启动即失败也必须记录原因并进入收集/清理流程。状态转换只能经 Controller 的事务接口执行，并追加事件。

例如：回答错误是 `finished + failed`；超时且远程停止未确认是 `timed_out + cleanup_unknown`。后者不得变成“安全完成”。

### 5.2 单次运行时序

1. 校验模型授权、环境、输入哈希、能力、私有资料隔离和剩余预算。
2. 在数据库创建 attempt、owner token、启动幂等键及启动意图，提交事务。
3. 建立独立环境，只导入公共输入；启动 Inspect 执行单元。
4. Adapter 发起启动并登记句柄；若响应丢失，先按幂等键查询，不盲目重发创建。
5. 记录心跳、运行进度和用量；所有真实模型请求先经过准入。
6. 正常结束或触发停止条件后，确认不会再修改产物；必要时请求停止并等待。
7. 导出回答、允许的文件与环境状态，建立不可变 manifest；失败时也收集可确认的部分结果。
8. 确认执行资源终止，清理本次拥有的临时资源；保留审计资料和快照。
9. 对稳定快照评分，追加 ScoreResult；更新报告。

文件收集必须发生在沙箱销毁之前。评分使用独立副本；如果产物无法稳定冻结，标记 `snapshot_unstable`，不能假装获得了可信最终交付。

### 5.3 取消、超时与资源回收

- `cancel` 首先撤销新模型请求的准入，再记录停止意图并向系统发取消请求。
- 第一版建议默认：取消宽限 15 秒、终止核验 30 秒；二者可配置，是工程默认值而非测得 SLA。
- 本地进程记录进程组及启动标识；先温和终止，再按授权强制终止；避免 PID 复用误杀。
- 容器按 owner label/句柄清理，禁止按模糊名称批量删除。
- 远程 BB 只使用验证过的取消/查询操作；HTTP 成功响应不等于所有 worker 已停止。
- 超时、取消或网关断连后，在途模型调用仍可能结算；账本不能立即清零或释放全部保留额。
- 无法确认停止时，记录资源清单并停止扩大该服务上的实验；由人工检查后解除，不自动忽略。
- `pause` 只停止派发新 run，默认允许已运行任务结束；不伪装成能冻结任意远程 Agent。

### 5.4 恢复与重试策略

| 发现的情况 | 恢复动作 |
|---|---|
| 已 terminal、快照和评分齐全 | 跳过，不重跑 |
| 已 terminal、有快照、未评分 | 只评分 |
| 仍运行、远端句柄可查询 | 重连并继续观测同一 attempt |
| 启动响应丢失 | 按幂等键查已有工作，找到后接管 |
| 启动是否成功无法确认 | `recovery_required`，停止自动创建和付费扩张 |
| 本地进程已死、有稳定部分产物 | 记录故障并收集；按预定策略决定是否新建 attempt |
| 租约过期但旧 worker 仍存活 | 先核对 owner token 和远端状态，不能双重接管 |
| 判分错误或答案错误 | 不重新执行 Agent；分别重评分或记失败 |

重试分层：传输重试、被测系统内部重试、整次 attempt 重试分别记录。第一版整次基础设施重试默认 **0 次**；需要时显式设置，建议最多 1 次。请求已发出但是否收费未知，必须保留不确定用量。

恢复的是实验控制流程；不承诺任意 Agent 的中间推理都能恢复。Inspect 的 Agent Checkpointing 有独立支持边界 [S12]，是否使用需针对选定 Agent 验证，不等同于 BB 的远程恢复。

## 6. Inspect Backend 与系统适配器

### 6.1 双层句柄，不做双重权威状态

- Controller 保存 `ExecutionHandle`，标识执行 worker、Inspect 日志和同一 attempt。
- Adapter 保存 `RunHandle`，标识被测系统内部的项目、进程或任务。
- 二者都持久化，控制器是逻辑状态的唯一写入权威；worker 只提交事件与结果。
- worker 失联时，独立恢复逻辑可据 RunHandle 查询远端，不依赖消失的内存对象。

### 6.2 Adapter 协议草案

下列为本项目待实现的接口设计，不是 Inspect 或 BB 已提供的 API：

```python
class SystemAdapter(Protocol):
    async def capabilities(self) -> AdapterCapabilities: ...
    async def start(self, request: StartRequest) -> RunHandle: ...
    async def reconnect(self, handle: RunHandle) -> RunHandle: ...
    async def poll(self, handle: RunHandle) -> RunObservation: ...
    async def cancel(self, handle: RunHandle, reason: StopReason) -> CancelAck: ...
    async def collect(self, handle: RunHandle) -> CollectedResult: ...
    async def cleanup(self, handle: RunHandle) -> CleanupResult: ...
```

`StartRequest` 只包含公共任务、可公开运行配置、环境句柄和短期运行凭据，不包含 GradingSpec。`collect` 和 `cleanup` 可重复调用且不改变已冻结产物；`cancel` 的返回值明确只是请求确认。

### 6.3 InspectBackend 执行策略

1. 公共 `TaskSpec` 转为 `Sample`；仅传公共 input/files/metadata，不填私有 `target`。
2. 构造单样本 Task，solver 负责调用 adapter，Agent 工具通过已批准环境执行。
3. 执行阶段关闭自动评分，私有评分器在后续可信进程加载；官方支持执行/评分分离 [S05]。
4. 将 Inspect log 的 ID、样本 ID、停止原因、模型记录与 run/attempt 关联。
5. 原生日志保存原状，业务结果通过规范化 importer 导入；不修改第三方内部日志格式。
6. 只接受白名单内、已版本固定的任务和评分模块，禁止从 Agent 产物导入 Python。

私有答案没有写进执行阶段的 `Sample.target`，因此评分不能假设日志中已经有正确答案。自定义 scorer 只在可信评分进程中，按固定 task/grader revision 从批准的注册表加载私有资料，读取 manifest 指向的冻结回答和产物；不向原始执行日志补写私有 target，也不依赖已被清理的沙箱路径。评分适配的准确调用方式由 SP05 在锁定版本上验证。

恢复远程运行时继续关联同一 attempt；如果必须重建 Inspect 上下文，新增日志段并在 attempt manifest 中关联，而非覆盖旧日志或再次触发 Agent 启动。是否可以直接重连该上下文属于 SP06 的验收内容。

第一版不用外层 Controller 和 `eval_set()` 同时自动重试同一工作。后续若采用 Eval Sets，须将它作为唯一的批次重试所有者，明确 ID 映射，并保留失败日志；其日志清理行为应显式配置 [S07]。

### 6.4 三类接入与四组基线

| Adapter | 第一版用途 | 关键实现点 |
|---|---|---|
| `MockAdapter` | 无模型测试、确定性失败注入 | 成功/错误答案/超时/取消失败/响应丢失等脚本化场景 |
| `PythonAdapter` | 自有单 Agent 和 planner-worker | 固定入口和工具契约；不可信代码不得在控制面同进程执行 |
| `CLIAdapter` | 跨语言最小示例与协议契约 | 固定 argv，不拼接 shell；输入/结果 JSON 分离，日志走独立通道 |
| `BBHttpAdapter` | commander/concerto | 共用客户端和映射逻辑，只切 system 配置，不 fork 两份适配器 |

A 组建议固定为一个推理循环、同一角色模型和工具权限。B 组建议采用一次集中式规划、有限 worker 执行、汇总输出；worker 上限作为配置公开，先用 2 个作为候选工程默认值。提示词、是否复核、模型角色和内部并发都必须冻结，不能在看过成绩后悄悄优化某组。

### 6.5 BB 专项接入清单

BB 具体 endpoint、认证、请求字段和终止状态尚未在本轮核验。不得据本文猜测 URL 或声称功能已经存在。

- 在实施时固定 BB commit/image，读取其对应 API 文档和测试。
- 验证创建项目是否会立即开始、是否需要确认目标、是否支持幂等键或外部标签查询。
- 验证上传公共资料、启动、模式配置及工具/模型权限的最小路径。
- 逐项映射等待人工、完成、失败、取消中的状态；默认无人协助，首次等待人工按协议记为 `blocked` 并停止。
- 验证最终产物、部分产物、用量明细、项目轨迹的获取与分页。
- 验证取消后所有 worker、后台模型调用和远程资源的实际状态。
- 检查项目间记忆、缓存、共享目录和全局配置；无法可靠清除时使用专用实例。
- 每个映射配匿名化 HTTP fixture 和契约测试；无法验证的能力保持 `unknown` 或黑盒等级。

## 7. 首批任务包和评分设计

### 7.1 五道候选题：用于工程试跑，不是已存在题库

采用合成、静态、可明确判分的材料。先避免网页实时变化、扫描 PDF/OCR 和 Office 排版差异；文件交付先使用 JSON、CSV、Markdown，后续再加 PDF/XLSX/DOCX。

| ID | 候选题 | 公共输入 | 规定交付 | 确定性评分重点 |
|---|---|---|---|---|
| T001 | 合同信息提取 | 一份带段落 ID 的合成合同 Markdown | `answer.json`，含 10 个约定字段及证据定位 | 字段类型、精确值、缺失值、引用对应 |
| T002 | 招标符合性矩阵 | 12 条明确要求、3 份合成供应商方案 | 36 行 `matrix.csv` + 汇总 JSON | 覆盖率、符合/不符合/材料不足、证据位置 |
| T003 | 订单对账 | 两份静态 CSV，包含重复键、差额及缺失记录 | `discrepancies.csv` + `summary.json` | 键匹配、异常集合、金额与总计、重复处理 |
| T004 | 多文档事实核对 | 4 份版本化说明与 8 个事实问题 | 答案 JSON + 证据清单 | 事实正确、版本优先规则、矛盾或资料不足识别 |
| T005 | 小型代码修复 | 一个无网络依赖的合成 Python 项目和缺陷说明 | 修改后的源文件/patch，保留公开测试 | 隐藏测试、回归测试、允许修改范围 |

题量、字段数、样本行数是候选 fixture 规格，需在出题时定稿，不是实测数据。每道题至少有正确产物、典型错误产物、缺失产物三个评分 fixture；开发任务与后续私有保留题分别管理。

### 7.2 任务包结构与公开/私有分离

```text
suites/smoke-v1/                  # 可以发布的公共定义
  suite.yaml
  tasks/T001/task.yaml
  tasks/T001/input/
  tasks/T001/output.schema.json
  ...

grader-store/smoke-v1/            # 运行时独立存储，不挂载给 Agent
  manifest.json
  T001/expected.json
  T002/expected.csv
  T005/hidden_tests/
```

上述是待创建结构。生产私有评分资料不得与公共仓库 checkout 一起挂载。测试仓库可以保存合成评分 fixture，但运行时必须验证只导出公共输入；“没有加入 prompt”不能替代文件系统隔离。

任务 YAML 必须表达 input manifest（相对路径、大小、哈希）、输出文件要求、公开评分口径、允许工具、网络要求和环境镜像。私有资料通过可信 grader manifest 按 task revision 关联，不进入 adapter payload。

### 7.3 判分策略

- 第一层验证交付存在、格式可解析、schema/列名/主键/路径符合要求。
- 第二层执行字段、集合、金额、证据位置或测试用例断言。
- 金额任务使用明确的小数与舍入规则；集合先按定义排序，不依赖任意行序。
- `null`、空字符串、`NA`、`N/A` 的语义由题目定义，不擅自互相转换。
- `passed` 条件在试跑前固定。候选默认：所有必需交付和关键断言通过；各维度分单独报告，不用平均分掩盖缺文件。
- 自由文本只在确定性规则不足时加入量表与盲审；模型评审在第一版默认关闭。
- 隐藏代码测试在隔离评分环境运行；被测代码仍不可信，不授予网络、主机目录或评分凭据。
- 输出“系统自报通过”和“外部通过”两列；不让 agent 输出的 success 字段直接成为 ScoreResult。

### 7.4 评分证据与离线重评分

每条规则记录 `rule_id、expected_ref、observed_ref、pass/fail、解释、权重（如有）`，聚合到 `ScoreResult`。评分失败分为有效交付未达标和评分器自身故障，后者记 `grader_error`。

重评分输入固定为 `snapshot_id + task_revision + grader_revision`。新规则产生新的 score ID，不覆盖旧记录。公开报告只展示允许公开的评分证据，隐藏答案与完整私有测试保持受控访问。

## 8. 用量、预算与模型接入

### 8.1 第一版实现范围

M0–M2 只使用假模型和确定性调用记录；M4 首先实现一个已选定提供方/协议的端到端准入，不一次实现通用多供应商代理。Bridge 或既有网关能复用的部分先验证，避免不必要自研。

所有真实运行须显式选择模型配置，记录提供方、模型标识、参数、角色映射、缓存策略、价格快照及可观测推理 token 口径。不得在连接失败时自动换模型、端点或凭据。

### 8.2 统一用量记录

每个模型请求保存：

```text
experiment_id, run_id, attempt_id, request_id, parent_request_id
agent_role, requested_model, resolved_model, provider_request_id
started_at, ended_at, status, retry_index, usage_source, usage_completeness
input_tokens_total, output_tokens_total, cached_input_tokens, reasoning_tokens
reserved_tokens, settled_tokens, cost_amount, currency, price_snapshot_id
raw_usage_ref, missing_reason
```

`input_tokens_total` 包含缓存输入；缓存量另列但不重复相加。输出和 reasoning 的包含关系按提供方规则适配，不能跨提供方直接叠加猜算。

```text
normalized_total = input_tokens_total + output_tokens_total
```

费用仅在原始用量和价格口径可确认时计算；否则为未知/估算并明确标注。执行、外部评分和控制面开销分账，同时进入整场实验总投入。

### 8.3 原子预留与结算

1. 在可信入口验证短期运行凭据，确定 run/attempt；不能只信任 Agent 自报的请求头。
2. 计算本次请求的可用输入估计/上界与输出上限；不接受无界最大输出配置。
3. 同一事务检查逻辑 run、实验执行池、整场授权总额以及金额上限（如有），创建 reservation。
4. 只有事务成功才向提供方转发；记录请求发送前后状态，处理崩溃窗口。
5. 收到用量后，以请求 ID 幂等结算；释放确认未使用部分。
6. 流式中断、请求已接受但用量未知时，标记 `settlement_pending`，保守保留额度，之后核对。
7. 超预算或估计被突破时拒绝新请求、发起停止、记录超额和原因，不能截断账本来假装未超预算。

准入条件采用保守计算：

```text
spent + active_reservations + requested_reservation <= authorized_limit
```

逻辑 run 的上限跨全部 attempt 累计，不因基础设施重试刷新；整场执行池和总池也同时扣减。评分有独立池，但同样受整场总额约束。并发测试使用可控假服务证明“同一份剩余额度不会被两个请求同时占用”。

### 8.4 不能宣称的保证

官方 token limit 不等于跨进程全角色原子预算 [S04]。本方案的准入设计也不能在输入无法界定、隐藏调用不可见、提供方用量缺失时保证真实费用绝不超出。

- 只周期性拉取 BB 累计用量：`observed`，只能做软停止。
- 所有请求经过准入但依赖估计：`admission_controlled`，披露误差与超额。
- 所有计量边界及最坏上界均已验证：才允许 `hard_bound_verified`。
- 被测系统仍持有绕开网关的真实提供方凭据：不能宣称受控预算，须移除或降级。
- 断线不代表提供方已停止计算；不能立即回收所有预留额度继续扩大发送。

### 8.5 小规模试跑的候选预算

延续原始计划的**规划上限**，不将其视为消耗预测：

| 项目 | 计算 | 候选 token 上限 |
|---|---|---:|
| 执行池 | 5 题 × 4 系统 × 1 次 × 250,000 | 5,000,000 |
| 机动池 | 执行池的 15% | 750,000 |
| 整场规划 | 执行池 + 机动池 | 5,750,000 |

机动池进一步明确评分与调试分配；确定性评分通常不产生模型 token，不能为用掉机动池而启动模型评审。15% 只是预算假设，不保证足够。后续 30 × 4 × 3 为 360 个逻辑运行，按同样上限执行池 90,000,000，含 15% 为 103,500,000；必须重新授权，不能沿用试跑权限。

## 9. 数据存储与产物管理

### 9.1 SQLite 最小表设计

第一版一个 Controller 负责调度状态写入；预算请求通过同一可信计量服务串行提交事务，不允许每个 Agent 各自维护余额。数据库置于 Linux 本地持久磁盘，不把共享网络盘作为默认部署方案。

| 表 | 核心字段 | 关键约束 |
|---|---|---|
| `experiments` | ID、配置快照/hash、授权记录、状态、代码/依赖版本、开始结束时间 | 配置快照不可变；一个实验独立输出目录 |
| `runs` | ID、experiment、task/system revision、repeat、当前 attempt、终态 | 实验内 task/system/repeat 唯一 |
| `attempts` | ID、run、序号、幂等键、句柄、phase/reason、owner/lease、时间 | 幂等键唯一；已终止尝试不重新启动 |
| `events` | 单调序号、event ID、关联 ID、类型、发生时间、payload | event ID 唯一；只追加 |
| `model_calls` | request ID、attempt、角色、状态、原始 usage ref、标准化用量 | 同一请求幂等更新；真实重试新建请求记录 |
| `reservations` | request ID、预算池、预留量、结算量、状态 | 请求和预算池唯一；防止重复扣费/释放 |
| `budget_pools` | 实验/逻辑 run/评分池、限额、累计支出、有效预留 | 多池检查与扣减必须在同一事务 |
| `snapshots` | ID、attempt、manifest ref/hash、稳定性、创建时间 | manifest 冻结后不变 |
| `scores` | ID、snapshot、grader revision、状态、维度分、证据 ref | 追加评分；报告固定引用具体 score ID |
| `resources` | owner、类型、外部 ID、stop 状态、cleanup 状态及证据 | 只能对匹配 owner 的资源执行清理 |

采用版本化迁移脚本、外键、必要唯一索引和事务。启用 WAL 与 busy timeout 的具体配置在集成测试中验证。网络请求不在数据库事务内等待；先持久化意图、执行外部操作、再记录结果，用幂等查询处理两者之间的崩溃窗口。

第一版不另建消息队列或通用事件溯源平台。`events` 作为审计和恢复辅助，权威当前状态保存在对应记录中；状态更新和事件追加必须一起提交。定期检查账本与 model_calls 对账，发现不一致即阻止继续付费派发。

### 9.2 输出目录契约

```text
output/<experiment_id>/
  config.resolved.yaml           # 去敏后的固定配置
  approval.json                  # 授权范围/配置 hash，不含密钥
  environment.json               # 代码、依赖、模型、镜像等版本
  state.sqlite
  events.jsonl                   # 从权威事件表导出的审计副本
  runs/<run_id>/attempts/<attempt_id>/
    result.json
    usage.jsonl
    adapter-events.jsonl
    inspect/                     # 原始 Inspect 日志，不原地重写
    snapshots/<snapshot_id>/
      manifest.json
      artifacts/
  scores/<score_id>/
    score.json
    evidence/
  reports/<report_id>/
    manifest.json                # 固定 score ID、统计方法、报告版本
    summary.md
    index.html
    runs.csv
    calls.csv
    scores.csv
    summary.json
```

这只是待实现的目录契约，不代表本次已经生成这些文件。报告采用相对证据链接，迁移完整实验目录后仍可追溯；数据库中的大文件引用避免写死开发者主目录。

### 9.3 产物收集与完整性

- 默认只收集任务规定的输出和必要诊断，不递归复制整个用户主目录或 BB 服务根目录。
- 拒绝越界路径、危险符号链接、设备文件及压缩包路径穿越；检查文件数、大小与总配额。
- 候选工程默认：单文件 20 MiB、单次产物总量 100 MiB，任务可显式覆盖；超限标记，不无声截断。
- 临时目录完成写入并核对 manifest 后原子发布快照；记录每项类型、字节数、hash 和来源。
- 对运行中的远程文件，优先使用服务提供的一致性快照；否则先确认停止再下载。无法确认时标记不稳定。
- 原始日志访问受控；公开报告生成去敏副本，不能用“去敏”名义无记录改写原始证据。
- 结果保留策略在运行前确定；清理临时环境与删除审计产物是两种不同操作，后者不自动执行。

## 10. CLI 和配置契约

### 10.1 第一版命令

**以下全部是拟开发接口，不是当前仓库已经可运行的命令。**

| 命令 | 行为 | 是否允许模型调用 |
|---|---|---|
| `agent-eval validate <config>` | 检查 schema、引用、哈希、能力和预算一致性 | 否 |
| `agent-eval dry-run <config>` | 展开矩阵，输出运行数、预算上限和阻塞项 | 否 |
| `agent-eval run <config>` | 创建实验并按授权执行；mock 模式不需要模型权限 | 真实模式必须已授权 |
| `agent-eval status <experiment-id>` | 展示阶段、结果计数、剩余预算、未回收资源 | 否 |
| `agent-eval pause <experiment-id>` | 停止派发，允许在途任务收尾 | 不新增 run；在途仍可能调用 |
| `agent-eval cancel <experiment-id>` | 停止派发、撤销准入、取消并核验资源 | 禁止新调用，追踪在途结算 |
| `agent-eval resume <experiment-id>` | 核对状态后继续未完成工作，不刷新预算 | 受原授权和剩余额度约束 |
| `agent-eval score <experiment-id> --grader-revision <rev>` | 对冻结快照追加评分 | 确定性评分无调用；模型评分需授权 |
| `agent-eval report <experiment-id>` | 由固定结果生成版本化报告 | 否 |

`validate`/`dry-run` 默认离线，不创建远程项目、不探测付费模型，也不执行配置中任意 Python。必要的环境健康检查单独以显式 `--probe` 启用，仍不允许模型生成请求。

命令输出支持人读与 `--json` 两种模式。建议退出码：0 表示命令操作成功，2 配置/能力错误，3 授权缺失，4 执行基础设施故障，5 停止或资源状态未确认；任务答错不伪装成 CLI 崩溃，若 CI 需要按验收失败退出，使用独立的 `--fail-on-score` 策略。

### 10.2 最先交付的无模型配置示例

```yaml
schema_version: "1"
name: smoke-mock-v1
suite: smoke-v1
systems:
  - mock-pass
  - mock-wrong-answer
repeats: 1
backend: inspect
model_profile: null
execution:
  mode: mock
  max_concurrent_runs: 1
  infrastructure_retries: 0
  wall_time_seconds: 60
  cancel_grace_seconds: 15
  termination_check_seconds: 30
limits:
  real_model_requests: 0
  experiment_total_tokens: 0
scoring:
  mode: deterministic
  grader_revision: smoke-v1.0
  model_calls_allowed: false
isolation:
  fresh_environment: true
  cross_run_memory: false
  network: deny
```

M1 先使用两道最小 fixture；五题齐备后上述 suite 对应 5 × 2 × 1 = 10 个 mock 运行。假用量用于预算测试时记入独立的 simulation 账本，不与真实用量或零费用运行混合。

`model_profile: null` 表示没有选择真实模型，不代表断言 Inspect 无需模型上下文。mock 模式由 Backend 显式选择 SP01 验证过的本地假模型实现，忽略真实模型默认配置，不读取提供方凭据；测试通过假入口请求计数和网络策略证明没有外部模型请求。

### 10.3 四组试跑配置模板

```yaml
schema_version: "1"
name: smoke-four-systems-v1
suite: smoke-v1
systems:
  - single-agent
  - planner-worker
  - bb-commander
  - bb-concerto
repeats: 1
backend: inspect
model_profile: null                 # 待用户确定；real 模式下 null 拒绝运行
execution:
  mode: real
  max_concurrent_runs: 1
  infrastructure_retries: 0
  wall_time_seconds: 1800
  cancel_grace_seconds: 15
  termination_check_seconds: 30
limits:
  total_tokens_per_run: 250000
  experiment_execution_tokens: 5000000
  experiment_grading_tokens: 0      # 第一版默认确定性评分
  experiment_debug_tokens: 750000   # 明确授权后才能使用，不自动挪作正式样本
  experiment_total_tokens: 5750000
  max_cost: null                    # 待确认金额与币种；真实付费启动前必填
budget:
  required_enforcement: admission_controlled
scoring:
  mode: deterministic
  grader_revision: smoke-v1.0
  model_calls_allowed: false
isolation:
  fresh_environment: true
  cross_run_memory: false
  task_network: deny
  model_network: approved_gateway_only
authorization:
  approval_ref: null                # 绑定配置 hash、模型、币种金额与 token 上限
```

这是**故意不能直接付费执行的模板**：模型、金额和批准记录未填齐，`run` 必须拒绝。若 BB 无法满足准入等级，可另建明确标为黑盒的实验；不能在校验失败后自动降级继续。

`task_network: deny` 禁止任务内容访问互联网，不代表禁止经过批准入口的模型请求；两类网络能力分别控制。配置哈希覆盖系统、题目、限额、工具、模型和环境，不能只对文件名签名。

### 10.4 授权与安全默认

- 正式授权记录应包含操作者确认、模型/端点、实验范围、token 和金额上限、时间范围、配置 hash。
- `authorization` 是本地审计门槛，不冒充多租户安全系统；操作系统权限与凭据管理仍负责真正访问控制。
- 修改模型、任务数、工具权限或预算后需要新的配置快照和授权；恢复不得隐式扩大范围。
- 被测 Agent 不能修改审批记录、预算数据库或提供方凭据。
- 未提供价格口径时，不声称能执行货币硬上限；正式付费运行先补足价格与计量验证。

## 11. 实验设计与报告

### 11.1 固定比较条件

1. 四组使用相同 task revision、公共输入和公开交付要求。
2. 第一轮尽量固定同一模型；如使用模型池，预先记录各角色映射及可用范围。
3. 外部工具能力、网络权限、总运行预算和最长时间保持一致；内部协作工具可以不同但需披露。
4. 默认每题新工作区，不继承上一题/其他组的记忆和产物；缓存策略固定并记录。
5. 任务—系统执行顺序采用固定种子的分块随机顺序；保留实际执行顺序和提供方异常。
6. 区分样本级并发与系统内部并发，记录 worker 数、CPU/内存上限和实际资源投入。
7. 评分版本在试跑前冻结；正式实验有未见保留题，不能用五道开发 fixture 宣称泛化能力。

A/B/C/D 比较回答“完整方案是否更好”。单独归因共享记忆、核验或去中心化时另设消融实验，不能从整体差异直接推出某一机制有效。

### 11.2 指标精确定义

| 指标 | 第一版口径 |
|---|---|
| 外部通过比例 | `passed logical runs / 全部预定 logical runs`，同时报告完成、未评分和中止数量 |
| 已评分通过率 | `passed / (passed + failed)`，与前者并列，不隐藏缺失项 |
| 任务质量维度 | 按类别展示字段准确率、覆盖率、测试通过等；不强行拼一个跨任务总分 |
| 执行 token/费用 | 统计全部 attempt、内部重试和失败调用；缺失用量单独列出 |
| 整场投入 | 执行 + 外部评分 + 调试/控制面，分别显示实测、估算与未知部分 |
| 每个成功运行成本 | 全部执行成本 / 外部通过数；通过数为零时 `null`，并解释不可计算 |
| 端到端时延 | 提交到得到可评分的稳定产物；另列排队、准备、执行、收集、评分和清理耗时 |
| 预算/超时触发率 | 触发对应原因的逻辑 run / 全部预定 run；基础设施重试不得丢失早先触发记录 |
| 错误自报比例 | 自报成功但外部判失败 / 可核验的自报成功数；缺失信号与未评分另列 |
| 清理确认率 | 已确认清理的已创建资源运行 / 已创建资源运行；零资源样本标 `not_applicable` |
| 用量覆盖 | 有可验证记录的调用/运行比例；调用总数未知时报告“无法确定”，不编造分母 |

用户取消导致未运行的样本保留 `not_started`；上表第一项是对整个预定实验的保守通过比例，不把所有未评分样本直接标记为“能力失败”。如果按预定策略允许 attempt 重试，主报告采用最后一个符合协议的有效终态，同时展示首次结果、全部尝试和累计成本，禁止取最好一次。

### 11.3 报告页面与导出

- **总览**：任务/系统版本、运行数、授权与实际预算、接入等级和数据完整性。
- **四组对比**：各任务类别质量、费用、时延与失败类型；不可比组单独呈现。
- **逐题详情**：各组结果、原始自报、外部评分、产物链接和关键证据。
- **运行审计**：尝试历史、取消/恢复事件、在途结算、未清理资源及数据缺口。
- **复现说明**：固定配置、镜像/依赖、任务哈希、评分版本、必要的非秘密环境配置。

CSV 保持一行一个逻辑 run，attempt 和模型调用分别输出关联表；金额、token、次数保持数值，未知留空并配原因列。HTML 转义不可信内容，CSV 的文本字段处理公式注入风险；原始输入仍保留在受控证据中。

### 11.4 正式实验的统计方法

阶段 3 再实现任务级配对分析：先在同一题内聚合预定重复，再比较系统差值；置信区间以任务为抽样单元，重复运行随任务一起保留。报告方法、随机种子、有效任务数和未评分处理，不能把同一道题三次运行当作三道独立题。

五题试跑只验证工程和口径，不做“哪个架构显著更强”的结论。30 题 × 3 次也是候选探索规模，不代表预先保证统计功效。

## 12. 安全、隔离与权限

### 12.1 威胁模型

把被测 Agent、公共输入中的指令、产物、代码和模型生成的 shell 命令均视为不可信。主要防护目标是隐藏评分资料、真实提供方凭据、控制面账本、其他运行和宿主文件；不承诺普通 Docker 设置能抵挡所有内核级攻击。

| 边界 | 必需措施 | 验证方式 |
|---|---|---|
| Agent → 私有答案 | 分进程/权限和挂载；private store 不可达 | 在隐藏区放 canary，检查文件、环境变量和序列化输入 |
| Agent → 宿主 | 仅挂载白名单工作区；不挂 Docker socket、主目录或整库 | 越界读取与符号链接攻击 fixture |
| Agent → 模型 | 短期 run-scoped token；提供方密钥只在可信入口 | 错误 run ID、过期 token、绕过端点测试 |
| Agent → 网络 | 任务 egress 与模型 egress 分离；提供方原生工具也需白名单 | 验证 web/MCP/远程工具不借模型接口绕开策略 |
| Agent → 评分器 | 冻结后隔离评分；限制 CPU/内存/磁盘/时间 | 恶意代码、资源耗尽、提示注入测试 |
| 报告 → 用户 | HTML 转义、禁不可信脚本、去敏导出 | XSS、CSV 公式、路径链接检查 |
| 清理 → 其他项目 | owner token + 精确资源标识 | 混入其他 owner 的资源不得被删除 |

官方 Bridge 文档特别说明，部分提供方工具在沙箱外执行，需要显式授予能力 [S03]。因此只封容器网络不等于封住模型侧搜索或远程 MCP；本项目将这些能力纳入 SystemSpec 和 validate。

### 12.2 凭据与日志

- 秘密通过宿主受控配置或秘密存储注入，不写入 YAML 示例、公共任务和 Git。
- HTTP 日志剔除认证头、token、签名查询参数；使用可审计的脱敏规则。
- 评分器只加载批准的本地实现；不执行外部日志指定的任意模块或模型端点。Inspect 官方也提示重评分会执行日志关联代码，应只处理可信日志 [S05]。
- 首次真实联调使用专用环境，不默认连接共享 bbtest，更不在生产服务做故障注入。
- 有敏感业务数据时单独确认授权、保存周期、对外报告范围及模型提供方的数据处理条件。

## 13. 工程结构、依赖和持续集成

### 13.1 建议目录

以下为实施时逐步建立的目录，不一次生成空壳模块：

```text
agent-testbed/
  pyproject.toml
  uv.lock
  README.md
  README.en.md
  agent.md
  src/agent_testbed/
    cli.py
    contracts/
    config/
    controller/
    backends/inspect/
    adapters/{mock,python,cli,bb}/
    environments/
    metering/
    storage/migrations/
    grading/
    reports/templates/
  suites/smoke-v1/
  systems/
  experiments/
  examples/fake_agent/
  tests/
    unit/
    contract/
    integration/
    e2e/
    fixtures/
  docs/
    development-plan.md
    architecture-decisions/
    compatibility/
    task-authoring.md
    adapter-authoring.md
    operations.md
  output/                         # 忽略运行产物
```

原始计划中的 `runner` 对应本文 `controller`，`graders` 对应 `grading`；只是模块命名收敛，不是增加另一套引擎。已有 README、图和协作规范保留，仅在实现后补真实使用入口。

### 13.2 候选技术栈

| 用途 | 候选选择 | 版本与替换策略 |
|---|---|---|
| 语言 | Python 3.12 作为首个验证目标 | 不声称是 Inspect 最低要求；M0 通过后固定支持范围 |
| 依赖管理 | `uv` + 锁文件 | 由项目选择并锁版本，不自动更换用户全局环境 |
| 数据契约 | Pydantic 2 | JSON Schema 导出、严格校验；具体版本随兼容性锁定 |
| CLI | Typer | 只实现本方案命令，避免早期插件框架 |
| HTTP | httpx | 统一超时、认证脱敏和显式重试规则 |
| 状态存储 | 标准库 sqlite3 + 迁移脚本 | 先不引入 ORM 和独立数据库服务 |
| 文件与配置 | PyYAML、标准库 JSON/hashlib/pathlib | YAML 安全加载，不支持任意对象构造 |
| 模板报告 | Jinja2 | 默认自动转义，静态输出 |
| 测试与质量 | pytest、pytest-asyncio、Ruff、mypy | 作为计划工具；未在本轮安装或执行 |
| 统计 | 先标准库；正式分析再评估 NumPy/SciPy | 不因“可能有用”一次引入大型分析依赖 |

所有第三方包在 M0 安装验证并审查许可证、锁定版本；这里是技术建议而不是已完成的兼容性结论。第一版支持 Linux/WSL CLI；Windows 原生进程取消和容器差异留待专项验证。

### 13.3 持续集成分层

- **PR 必跑**：格式/静态检查、schema、单元、adapter mock 契约和零模型端到端测试；不配置付费模型凭据。
- **受控环境集成**：Docker staging、快照导出、进程取消、重启恢复；没有 Docker 的环境显式 skip 并说明，不能当作通过。
- **手动真实联调**：仅在专用环境、具备批准记录时执行；不会由 fork PR、定时任务或合并动作自动启动付费模型。
- **发布验证**：从干净环境按锁文件安装，运行 mock 示例并核对导出；生成包和版本信息，不擅自发布到公共索引。
- **日志保留**：失败测试保留去敏证据；成功测试不把私有答案、凭据、运行输出提交到仓库。

以下仅是工程配置完成后预期执行的检查命令，本轮没有执行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/agent_testbed
uv run pytest -m "not paid and not requires_docker"
uv run pytest -m "requires_docker and not paid"
```

## 14. 测试矩阵

| ID | 场景 | 核心断言 | 最早门槛 |
|---|---|---|---|
| CT01 | schema 往返和版本不兼容 | 必需字段、类型、未知枚举被正确处理 | M1 |
| CT02 | 矩阵展开 | 5 × 4 × 1 恰为 20 个稳定逻辑 run；重复展开不重复插入 | M1 |
| CT03 | 正常完成与错误答案 | 均可 `finished`，但外部评分分别 pass/fail | M1 |
| CT04 | 超时与进程树 | 到期停止派发/调用，子进程无遗漏或明确标 unknown | M2 |
| CT05 | 取消请求被接受但任务未停 | 不提前宣告 cleanup confirmed | M2 |
| CT06 | 创建成功、响应丢失 | 按幂等键发现原工作，不二次创建 | M2 |
| CT07 | Controller 在多个时点崩溃 | 启动前/启动后/收集后/评分前均可正确核对恢复 | M2 |
| CT08 | 多控制器或旧 worker 竞争 | 租约和 owner fencing 阻止重复派发/跨 run 写入 | M2 |
| CT09 | 清理重试 | 幂等且只处理本次 owner 的资源 | M2 |
| CT10 | 沙箱销毁与产物 | 在销毁前导出；快照后无变化，hash 校验一致 | M2 |
| CT11 | 隐藏答案及凭据 canary | Agent 输入、环境、挂载及日志可见面无泄漏 | M2 |
| CT12 | 重评分 | 不启动 Agent、不新增执行请求，保留旧分数 | M2 |
| CT13 | BB 状态/分页/中断映射 | 对应固定版本 fixture；等待人工不被算作完成 | M3 |
| CT14 | Bridge/网关角色覆盖 | planner/worker/verifier/摘要调用都有归属，漏记可检测 | M4 |
| CT15 | 并发最后一份余额 | 多请求竞争时不重复预留，拒绝发生在转发前 | M4 |
| CT16 | 预留后崩溃及流式取消 | 已发/未发/未知可区分，未知额度不错误释放 | M4 |
| CT17 | 重复 usage 与提供方重试 | 重复回报不双计，实际重试成本不漏计 | M4 |
| CT18 | 多 attempt 总预算 | 新尝试不重置 run/experiment 限额 | M4 |
| CT19 | 缺失用量和零成功 | 未知不填零；零成功成本比为 null | M4 |
| CT20 | 不可信文件和报告 | 路径穿越、符号链接、XSS、CSV 注入被阻断 | M2/M4 |
| CT21 | 四系统 fake 全流程 | 20 个逻辑 run，状态/评分/证据/预算逐项可对账 | M4 |
| CT22 | 明确授权后真实试跑 | 四组同题，所有失败保留，停止与费用可说明 | M5 |

每项测试需保存可重放 fixture 或命令及断言。关键安全/预算路径以具体反例测试为验收，不用一个总体覆盖率数字代替。CT14 中“发现隐藏调用”取决于可验证的端点/凭据/网络边界，无法证明总调用范围时必须降级，不能仅靠检查已收到的日志证明完整。

## 15. 分阶段工作包与验收

### 15.1 总体里程碑

工时为 **1 名熟悉 Python/Agent 的开发者，在专用环境与 BB 文档及时可用的前提下**的粗估，不含用户决策等待、复杂服务改造和正式评测费用；不是交付承诺。

| 里程碑 | 对应原计划 | 主要交付 | 依赖 | 粗估有效开发日 |
|---|---|---|---|---:|
| M0 技术验证 | 阶段 0 前置 | 六项 spike、依赖锁、关键 ADR | 无 | 2–3 |
| M1 最小闭环 | 阶段 0 | 协议、mock、两题、规则评分、基本 CLI/报告 | M0 | 3–4 |
| M2 可靠性与隔离 | 阶段 0 | SQLite 恢复、取消/清理、快照和安全测试 | M1 | 4–6 |
| M3 真实系统适配 | 阶段 1 | 单 Agent、BB 两模式、CLI 契约示例 | M2 | 5–8 |
| M4 四组与预算 | 阶段 2 前半 | planner-worker、五题、准入账本、完整报告 | M2；集成依赖 M3 | 5–7 |
| M5 授权试跑 | 阶段 2 后半 | 20-run 试跑、问题修复、可复跑交付 | M3/M4 + 用户授权 | 2–4 |
| M6 正式与扩展 | 阶段 3/4 | 公开任务集、正式重复、统计/消融 | M5 结果与新授权 | 另行估算 |

M0–M5 合计 **21–32 个有效开发日**。考虑联调风险，可按约 5–8 个自然工作周规划，具体取决于投入强度和阻塞项；不得把这项估算当成已经确认的排期。

### 15.2 M0：验证底座，固定不能猜的部分

交付：

- 候选 Inspect 版本的最小任务、文件、停止、Bridge、评分和日志验证记录。
- 明确 Task cleanup/沙箱销毁时机、日志导出和模型调用检查点。
- 确定 Python/依赖锁与许可证记录。
- ADR-001：单 attempt 单样本执行与控制器权责。
- ADR-002：公共任务与私有评分隔离。
- ADR-003：计量入口和预算等级。

**门槛**：SP01–SP06 有真实结果或清楚的未通过结论；没有真实模型生成费用。若关键能力不满足，先缩减支持范围或修改设计，不把未验证部分隐藏到后续。

### 15.3 M1：第一个用户可见闭环

依次实现：核心 schema → 配置解析/矩阵 → MockAdapter → InspectBackend 最小执行 → 两个确定性 grader → Markdown/JSON 报告。

**演示**：两题 × mock-pass/mock-wrong-answer × 一次；用户能看到同一任务被两个系统完成并得到不同外部分数。输出的 run ID、日志、产物和评分互相可定位。

**门槛**：`validate`、`dry-run`、mock `run`、`score`、`report` 可用；零外部模型请求、错误答案不自动重跑；此阶段不要求 BB 接入和完整预算网关。

### 15.4 M2：可靠性先于烧模型

实现状态持久化、意图日志、幂等查询、暂停/取消、恢复、owner 检查、产物冻结和私有评分边界；新增失败注入的假 CLI/HTTP 服务。

**门槛**：CT04–CT12 通过；连续恢复不会多创建工作；未知资源不会被标成已清理；完成产物不会因 sandbox 删除而丢失；评分器升级无需重新执行 Agent。

### 15.5 M3：接入系统而不改核心

- 单 Agent 通过 PythonAdapter 接入固定工具与模型配置。
- BBHttpAdapter 共用 commander/concerto 路径，完成状态、产物、取消与用量映射。
- CLIAdapter 提供无模型跨语言示例，先证明协议边界，不追求接入多个外部框架。
- 完成系统 capability manifest 和限制说明。

**门槛**：适配器 mock/录制响应契约通过；真实系统端到端验收须获得小额联调授权，否则标记“待真实验证”，不得宣称 M3 完成。黑盒能交付不等于受控合格。

### 15.6 M4：预算、任务和报告闭合

- 完成固定策略 planner-worker，冻结四组配置及提示词。
- 实现五题及每题评分 fixture，出题与评分进行交叉复核。
- 完成一个真实模型协议的计量适配、并发预留/结算、价格快照和完整性检查。
- 实现完整 Markdown/HTML/CSV/JSON 导出、数据缺口和资源审计。
- 跑四个假系统的 20-run 回归，验证整个矩阵和全部账本。

**门槛**：CT14–CT21 通过；费用或用量未知可见；超预算停止可解释；若远端模型入口不可控，则明确切分黑盒实验，不发布同预算结论。

### 15.7 M5：用户授权后的首轮试跑

1. 固定五题、四组、模型、工具、环境、评分和预算，生成 dry-run 清单。
2. 获得绑定配置 hash 的预算及专用环境授权。
3. 先运行小批次检查停止/用量/产物，再继续剩余预定矩阵；同一预定 run 不因抽查重复计入。
4. 任一泄漏、越权、未知持续收费或严重账本不一致即停止扩张，保留现场。
5. 汇总 20-run 结果，区分工程缺陷、被测能力失败和评分问题。
6. 修复后需重跑时新建有版本的实验，保留原实验；不混合挑选最优结果。

**门槛**：每个预定 run 都有明确状态；每个最终结果能定位产物与评分；费用缺口、遗留资源和失败均已解释。最后交付的是可信试跑报告，不是“证明多 Agent 更好”。

### 15.8 M6：按证据扩展

仅在试跑稳定、预算重新授权后开展：一个公开评测集接入 → 更广题目/重复 → 配对分析 → 消融和故障注入。优先选择符合研究目标和工具能力、许可可用的任务子集；不预先承诺 GAIA、SWE-bench 或其他公开集已经兼容。

Web 平台、分布式调度、多提供方通用网关和更多文件格式，只有在现有 CLI/静态报告确实成为瓶颈后再单独立项。

## 16. 第一批可执行开发任务

以下为建议 issue/PR 切分，不代表已经创建 issue、提交代码或获得 Git 提交授权。

| 编号 | 最小工作包 | 主要落点 | 完成标准 | 依赖 |
|---|---|---|---|---|
| DEV-001 | 验证 Inspect 基础执行与版本 | `docs/compatibility/`、临时验证示例 | 固定版本无模型任务有原生日志 | 无 |
| DEV-002 | 建 Python 工程与零付费 CI | `pyproject.toml`、锁文件、`tests/` | 干净环境安装、测试运行且无模型密钥 | DEV-001 |
| DEV-003 | 定义公共/私有 schema | `contracts/` | JSON round-trip、禁止私有字段进入 StartRequest | DEV-002 |
| DEV-004 | 配置校验与矩阵展开 | `config/`、`cli.py` | `validate/dry-run` 离线，矩阵和预算准确 | DEV-003 |
| DEV-005 | MockAdapter 与两题 fixture | `adapters/mock/`、`tests/fixtures/` | 成功/错误答案/超时可确定重放 | DEV-003 |
| DEV-006 | 单 attempt InspectBackend | `backends/inspect/` | 一个样本日志关联一个 attempt，评分延后 | DEV-004/005 |
| DEV-007 | 产物快照与两个 grader | `storage/`、`grading/` | 稳定 hash、明确 pass/fail、可重评分 | DEV-006 |
| DEV-008 | 首个 Markdown/JSON 报告 | `reports/` | 从汇总点击到逐题证据，无虚构结果 | DEV-007 |
| DEV-009 | SQLite 状态与恢复 | `controller/`、`storage/` | 响应丢失/重启不重复启动 | DEV-006 |
| DEV-010 | 取消、资源 owner 与泄漏测试 | `environments/`、`tests/integration/` | 未停不报清理成功；canary 不泄漏 | DEV-007/009 |
| DEV-011 | BB API 兼容性核对与 fixture | `adapters/bb/`、`docs/compatibility/` | 固定版本、每个能力有证据 | DEV-003，可与 M2 部分并行 |
| DEV-012 | 预算账本与假并发服务 | `metering/` | 原子预留、未知结算、跨 attempt 限额均通过 | DEV-009 |

**建议第一轮只实现 DEV-001～DEV-008**，先交付真正能跑的无模型闭环；随后集中完成 DEV-009/010 的可靠性。不要第一天就同时写 BB、通用网关、全部任务集和 Web 页面。

人员多于一名时，协议冻结后可以并行：开发 A 负责 Controller/Storage，开发 B 负责 Inspect/Adapter，任务作者负责 fixture/Grader；三者以 contracts 和版本化测试数据为协作边界。当前没有指定真实负责人，以上只是角色分工建议。

## 17. 风险、决策门槛与待确认事项

| 风险 | 提前检测 | 降级/阻塞处理 |
|---|---|---|
| Inspect 接口随版本变化 | 六项 spike + 依赖锁 + 日志 fixture | 修兼容层；不直接跟随最新版本 |
| BB 不支持幂等查询/真实停止 | 创建响应丢失和取消契约测试 | 不自动重试；未知状态阻塞继续运行 |
| 模型调用绕过统一入口 | 检查凭据、网络、角色和假请求计数 | 降为黑盒，拒绝严格预算实验 |
| 双层重试和自动 SDK 重试漏记 | 模拟提供方超时/限流并数实际请求 | 明确唯一重试所有者，每次转发都有记录 |
| 出题规则模糊 | 正确/错误/缺失产物交叉判分 | 先修任务和 grader，再固定实验版本 |
| 五题只代表工程样例 | 报告明确限制 | 扩大未见任务后再讨论架构优势 |
| 评分资料可被 Agent 读取 | canary 与实际挂载/网络检查 | 作为安全阻断项，不以提示词约束替代 |
| 长任务意外持续收费 | 在途账本、取消确认和资源核对 | 停止派发、撤销凭据并人工核对 |
| 工程过度设计 | 每个里程碑必须有可演示产物 | 暂缓 Web、集群和通用插件分发 |

### 17.1 不阻塞零模型开发的默认决定

- 在当前 `agent-testbed` 仓库实施，保留现有许可证和协作约定。
- 先用合成任务、确定性评分、单样本并发和假服务。
- 当前只产出开发方案；不自动安装依赖、改 BB 服务、创建真实模型账户或启动收费实验。
- 许可证文件存在不代表公司代码和业务数据已获得公开授权，后续接入时分别确认。

### 17.2 真实联调前必须由用户确定

1. BB 专用测试环境、准确版本、可用认证方式及允许访问的 API。
2. 模型/提供方、批准端点、角色映射、价格口径，以及真实请求入口是否可控。
3. 首批五题是否采用本文合成候选题；若换业务材料，确认来源与使用权限。
4. 小额联调和正式试跑的 token、费用币种/上限、并发与最长执行时间。
5. 日志/产物保存范围、保存周期、报告公开程度和敏感信息规则。

在这些事项确认前，可以完成工程、协议、假服务、任务草案和安全测试；不能将未确认配置替用户补成可付费运行的默认值。

## 18. 第一版发布检查表

### 工程与可扩展性

- [ ] 干净 Linux/WSL 环境按锁文件安装并运行无模型示例。
- [ ] 新增被测架构只增加适配器和配置，不改 Controller 或评分器。
- [ ] 新增同类型任务不改架构适配器，公共/私有资料边界清晰。
- [ ] Inspect、BB、模型、任务、提示词、环境和 grader 版本可追溯。

### 结果与可靠性

- [ ] 执行状态、系统自报、外部评分和资源状态独立记录。
- [ ] 超时、取消、响应丢失、重启恢复和清理有实际通过的测试。
- [ ] 不重复启动不确定的远程工作，不删除其他 owner 的资源。
- [ ] 产物在环境销毁前冻结，重评分不需要重跑 Agent。
- [ ] 原始执行、失败尝试和旧版评分不被覆盖。

### 计量与安全

- [ ] 预算覆盖整个系统及内部重试，跨 attempt 不刷新逻辑 run 上限。
- [ ] 并发准入与在途结算有反例测试，预算等级按证据声明。
- [ ] 未知用量、不可计算成本和未知清理状态不填零或标成功。
- [ ] 隐藏答案、提供方凭据和控制面账本不进入 Agent 可读边界。
- [ ] 模型侧工具、任务网络与外部服务权限纳入实验配置。

### 用户可见交付

- [ ] 两类以上任务结果可独立验收，并含五道版本固定的试跑题。
- [ ] 四组 20-run 矩阵在获批后完整执行，所有预定样本均有明确状态。
- [ ] 报告涵盖质量、成本、耗时、失败、数据缺口和资源回收。
- [ ] 黑盒与受控结果分开，不声称五题证明架构优越性。
- [ ] CLI、任务编写、适配器接入和运行维护说明与真实实现一致。

## 19. 参考资料与验证说明

### 19.1 官方参考索引

以下页面于 **2026-11-16** 查阅；它们说明 Inspect 当前在线文档中的能力，不替代 M0 对锁定发行版本的实际测试。正文 `[Sxx]` 对应此表。URL 以代码格式保留，便于在版本升级时复核。

| 编号 | 官方页面 | 主要核对内容 | URL |
|---|---|---|---|
| S01 | Tasks | Task 组成、solver 与 cleanup | `https://inspect.aisi.org.uk/tasks.html` |
| S02 | Datasets | Sample 字段、文件、metadata、target | `https://inspect.aisi.org.uk/datasets.html` |
| S03 | Agent Bridge | 同进程/沙箱 Bridge、代理范围、授权工具 | `https://inspect.aisi.org.uk/agent-bridge.html` |
| S04 | Setting Limits | token/time/cost 限制及检查语义 | `https://inspect.aisi.org.uk/setting-limits.html` |
| S05 | Scoring Workflow | 延迟评分、重评分、日志可信边界 | `https://inspect.aisi.org.uk/scoring-workflow.html` |
| S06 | Scorers | 规则、自定义评分与指标 | `https://inspect.aisi.org.uk/scorers.html` |
| S07 | Eval Sets | 重试、样本复用、继续执行和失败日志清理 | `https://inspect.aisi.org.uk/eval-sets.html` |
| S08 | Sandboxing | 工具执行位置、沙箱、文件和清理 | `https://inspect.aisi.org.uk/sandboxing.html` |
| S09 | Log Files | 原生日志、读取和结果追溯 | `https://inspect.aisi.org.uk/eval-logs.html` |
| S10 | Log Viewer | Inspect View 与逐样本查看 | `https://inspect.aisi.org.uk/log-viewer.html` |
| S11 | Using Agents | 原生 Agent 的接入概念；非第一版强制架构 | `https://inspect.aisi.org.uk/agents.html` |
| S12 | Agent Checkpointing | 明确支持的中间状态恢复，不保存任意外部副作用 | `https://inspect.aisi.org.uk/checkpointing.html` |

### 19.2 本次完成与未完成的边界

本次完成：阅读现有仓库约定、原始计划与 Inspect 官方说明；撰写本开发设计。实际执行的静态检查包括：19 节编号连续性、代码围栏闭合、表格列数、12 个来源编号及本地链接、3 个 YAML 示例解析、Python 接口示例语法、22 项测试编号、12 个工作包编号、试跑/正式实验预算与工时加总，以及 Git 空白字符检查。以上均为文档检查，不是应用功能或 Inspect 兼容性测试。

本次未做：未安装/锁定运行依赖，未编写应用实现，未调用 BB，未运行任何真实模型或付费评测，未执行 Git 提交/推送，未验证实际 API 兼容性。本文中的命令、目录、schema、工时和任务内容均明确为开发设计或候选值。

**推荐批准的下一步：仅启动 M0/M1 的无模型开发，先交付可演示的执行—评分—报告闭环。**
