# M2 使用说明与已知边界

日期：2026-11-17。当前交付是一个**仅运行受信任确定性 mock 的本地可靠性闭环**，不是生产 Agent 评测服务。开发路线见 [详细方案](<development-plan.md>)，实测环境见 [兼容性记录](<compatibility/inspect-m0.md>)。

## 1. 安装

需要 Python 3.12 和 uv。项目只声明经过验证的 Python 3.12 支持；不修改机器的全局解释器或模型配置。

```bash
uv sync --locked --group dev
```

本次开发将 uv 0.13.8 放在忽略目录 `.tools/uv`，没有全局安装 uv。若本机尚无 `uv`，可用该项目内工具执行同一命令：

```bash
.tools/uv sync --locked --group dev
```

依赖下载需要网络；**安装后，默认测试、配置验证和 mock 演示均不需要互联网或模型凭据**。测试默认通过 pytest-socket 禁止 TCP socket，允许 asyncio 所需 Unix socket；执行另有 Python socket guard。它们是回归保护，不是运行恶意代码的 OS 沙箱。

## 2. 跑第一轮实验

从仓库根目录执行：

```bash
uv run agent-eval validate experiments/smoke-mock.yaml
uv run agent-eval dry-run experiments/smoke-mock.yaml
uv run agent-eval run experiments/smoke-mock.yaml
```

CLI 输出 JSON，包含新实验目录和 Markdown 报告路径。每次 `run` 创建新的实验 ID，不覆盖之前实验。

默认配置在 [smoke-mock.yaml](<../experiments/smoke-mock.yaml>)，任务集在 [suite.yaml](<../suites/smoke-m1/suite.yaml>)：

| 题目 | 交付 | mock-pass | mock-wrong-answer |
|---|---|---|---|
| T001 合同字段提取 | JSON 中六个字段及段落证据 | 正确提取 | 故意缺一个必需字段 |
| T003 订单对账 | CSV 异常行，含重复键、差额和单边缺失 | 完整异常集合 | 故意仅输出表头 |

四个运行都自报成功、都正常执行结束；独立评分应给出 **2 passed / 2 failed**。错误 mock 不会被重试到答对。模型费用只表示真实模型调用为零，不包含主机和开发时间。

这是原始五题计划中 T001/T003 的 M1 精简 fixture，不声称五题套件或四个真实架构已接入。

## 3. 查看、延迟评分和重评分

以下 `output/exp-...` 换成 CLI 实际返回的目录：

```bash
uv run agent-eval status output/exp-...
uv run agent-eval run experiments/smoke-mock.yaml --no-score
uv run agent-eval score output/exp-... \
  --grading tests/fixtures/grading/smoke-m1/grading.yaml
uv run agent-eval report output/exp-...
```

需要固定某批评分时：

```bash
uv run agent-eval report output/exp-... \
  --score-batch output/exp-.../scores/scorebatch-.../manifest.json
```

每次评分新增 score batch；每次报告记录其使用的 score ID 和 batch 哈希。默认选择 `created_at` 最新的完整评分批次，不修改旧报告、旧评分或原始 Inspect 日志。没有评分时显示 `unscored`，零通过样本的单位成功成本为 `null`。

当前两个业务 grader 使用纯 Python/JSON Schema/CSV 校验，直接读取稳定快照。原生 Inspect 离线评分 API 已有单独兼容性测试；没有为了统一外观而把私有答案塞回 Agent 执行日志。

## 4. 证据组织

每轮实验包括：

- `plan.json`：执行前固定的 run 矩阵；中断时仍可查询。
- `config.resolved.json`、`environment.json`：配置和版本。
- `runs/<run>/attempts/<attempt>/inspect/`：真实 Inspect `.eval` 日志。
- `snapshot/manifest.json` 和 artifacts：内容哈希与冻结交付。
- `result.json`：执行状态、自报、用量、日志和快照引用。
- `scores/`：不覆盖旧记录的独立评分批次。
- `reports/`：JSON、Markdown 及其证据链接。

快照采用“不覆盖写入 + 哈希复核”，不是不可篡改存储或抵御同用户恶意进程的安全保证。篡改产物后重评分会记录 `grader_error`；原始结果记录遭篡改时命令拒绝继续。

M2 运行还会生成 `state.sqlite`：

- `runs`、`attempts`、`resources` 和只追加 `events` 使用 SQLite 事务保存。
- 启动前先 claim run 和幂等键，登记 PID、进程启动时间、boot ID 与 deadline 后，再通过管道放行 worker。登记前控制器死亡会关闭管道，worker 不执行任务。
- 一个实验只允许一个驱动控制器持有自动释放的本地文件锁；第二个 `resume` 会被明确拒绝。暂停、取消仍可以从另一个 CLI 提交控制请求。
- worker 只加载公共 `request.json`，环境不继承模型凭据；它生成 `.eval` 和原始 outcome。快照发布、数据库转移和最终索引由控制器负责。
- SQLite schema v1 自动迁移到 v2 并保留事件；没有可验证身份的旧活跃 worker 会阻止自动恢复，不能凭 PID 猜测安全状态。
- 运行结果与报告关联不可覆盖的执行 manifest；恢复后更新当前索引，不改变旧报告使用的证据。

## 5. 暂停、取消与恢复

以下命令只适用于带 `state.sqlite` 的 M2 实验：

```bash
uv run agent-eval pause output/exp-...
uv run agent-eval status output/exp-...
uv run agent-eval resume output/exp-...
uv run agent-eval cancel output/exp-...
```

`pause` 停止派发新的 run，当前本地 worker 自然完成后控制器退出；`resume` 将暂停状态切回 running，继续未派发任务。`cancel` 先持久化请求，活跃控制器负责终止原进程组；若原控制器已死亡，取消命令核对身份后接管清理。状态可能先显示 `cancel_requested`，只有确认进程组无活跃成员后才显示 `cancelled`。无法确认时显示 `recovery_required` 和未知资源，阻止后续派发。

控制器被强制终止后，`resume` 接管同一个 attempt：worker 仍存活就继续观测到完成或 deadline；已退出就收集已有 outcome 和部分产物。快照/结果已发布而数据库尚未提交时，复用原快照和结果，不重复启动、不覆盖证据。

默认 `infrastructure_retries: 0`：被杀死且没有 outcome 的 attempt 记 `infra_error`，不会重排队成成功；已完成、错误答案和超时样本均不自动重跑。真正未派发的任务才继续执行。重复 `resume` 不新增 attempt 或相同自动评分批次；需要重新评分时使用独立 `score` 命令。配置、输入或评分版本变化会拒绝恢复；已取消实验不能恢复。

本地资源控制覆盖同一 session/进程组中的子进程，包括 leader 先退出的情况；不覆盖主动脱离 session 的恶意进程。当前恢复的是单机 Linux/WSL 的执行生命周期，不是任意 Agent 的内存推理；远程 BB 和 Docker 资源仍未接入。

## 6. 修改任务和评分

- 公共 task 配置不允许添加 `expected_answer` 等未声明字段。
- 输入材料和评分材料分别有 SHA-256，修改材料必须同步修订哈希与版本。
- mock 只读取已经 stage 的公共输入，评分材料不会复制进该输入目录。
- 默认评分文件是**公开的合成测试答案**，见 [grading.yaml](<../tests/fixtures/grading/smoke-m1/grading.yaml>)；它不适用于私有保留题。
- mock 在独立进程中运行，与控制器仍是同一 OS 用户。虽然没有向它传入评分资料和模型凭据，恶意代码仍可能读取宿主文件。当前进程隔离不能代替 Docker/OS 安全边界，不允许引入真正私有答案或不可信 Agent。
- JSON schema 禁止外部引用，防止校验阶段隐式联网；YAML 禁止重复键和任意对象构造。
- task 文件中的 `fixture_driver` 是已注册 mock 计算逻辑，不是通用 Agent 插件入口。

## 7. 测试

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/agent_testbed
uv run pytest -q
```

默认 Docker 探针明确 skip。只在符合要求的专用环境中选择运行：

```bash
TESTBED_DOCKER_PROBE=1 uv run pytest -q tests/test_inspect_docker.py
```

该探针只使用已存在的镜像（默认 `python:3.12-slim-bookworm`），禁止 pull、不挂宿主目录、网络关闭，并核对自己创建的容器已清理。当前宿主 Docker 20.10.20 不满足锁定 Inspect 的 Docker Engine >=24.0.6 要求，实测失败发生在创建容器之前；没有为使测试通过而修改全局 Docker 或绕过检查。

CI 配置见 [ci.yml](<../.github/workflows/ci.yml>)，只进行无模型检查和演示。本轮没有提交推送或核验远端 CI；不能将本地测试结果当作远端通过记录。

## 8. 当前不支持

- 真实模型、BB、planner-worker、HTTP/CLI 外部系统接入。
- 真实预算准入、计费核对、并发实验。
- 生产隔离、保密评分存储、真实业务数据。
- HTML 报告、统计显著性分析、公开 Benchmark 批量接入。

当前已支持本地 mock 的暂停、取消、恢复和状态持久化；仍不支持远程幂等启动、BB 取消、Docker 资源回收或任意 Agent 推理状态恢复。

相应配置会被拒绝，不会自动切真实模型、偷偷增加预算或降低能力等级。
