# M1 使用说明与已知边界

日期：2026-11-16。当前交付是一个**仅运行受信任确定性 mock 的工程闭环**，不是生产 Agent 评测服务。开发路线见 [详细方案](<development-plan.md>)，实测环境见 [兼容性记录](<compatibility/inspect-m0.md>)。

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

快照采用“不覆盖写入 + 哈希复核”，不是不可篡改存储或抵御同用户恶意进程的安全保证。篡改产物后重评分会记录 `grader_error`；原始结果记录遭篡改时命令拒绝继续。M1 使用 JSON 文件记录，不含 SQLite 事务恢复。

## 5. 修改任务和评分

- 公共 task 配置不允许添加 `expected_answer` 等未声明字段。
- 输入材料和评分材料分别有 SHA-256，修改材料必须同步修订哈希与版本。
- mock 只读取已经 stage 的公共输入，评分材料不会复制进该输入目录。
- 默认评分文件是**公开的合成测试答案**，见 [grading.yaml](<../tests/fixtures/grading/smoke-m1/grading.yaml>)；它不适用于私有保留题。
- 当前 mock 与控制器同进程、同用户，不能阻止恶意代码读取仓库。只有完成 M2 的 OS/容器隔离，才可引入真正私有答案和不可信 Agent。
- JSON schema 禁止外部引用，防止校验阶段隐式联网；YAML 禁止重复键和任意对象构造。
- task 文件中的 `fixture_driver` 是已注册 mock 计算逻辑，不是通用 Agent 插件入口。

## 6. 测试

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

CI 配置见 [ci.yml](<../.github/workflows/ci.yml>)，只进行无模型检查和演示。远端 CI 尚未触发，不能把本地通过描述为 GitHub Actions 已通过。

## 7. 当前不支持

- 真实模型、BB、planner-worker、HTTP/CLI 外部系统接入。
- 真实预算准入、计费核对、并发实验。
- 暂停/取消/恢复 CLI、远程幂等启动、进程树和资源回收保证。
- 生产隔离、保密评分存储、真实业务数据。
- HTML 报告、统计显著性分析、公开 Benchmark 批量接入。

相应配置会被拒绝，不会自动切真实模型、偷偷增加预算或降低能力等级。中断后 `status` 返回 `incomplete`，保留证据但不自动重跑；恢复能力按 M2 实施。
