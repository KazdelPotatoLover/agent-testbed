# Inspect M0 兼容性记录

日期：2026-11-17（M2 本地验证更新）。**结论：Inspect 支持当前受信任 mock 闭环；M2 本地可靠性已经验证，Docker 强隔离和远程系统仍未通过验收。**

## 固定环境

| 项目 | 本轮实际值 |
|---|---|
| Python | 3.12.16；使用已有解释器建立项目 `.venv`，未改全局 Python |
| Inspect AI | 0.3.290，直接依赖精确固定 |
| uv | 0.13.8，项目内工具，下载文件核对 PyPI SHA-256 |
| OpenAI SDK（仅 Bridge 测试） | 3.51.0，开发依赖；没有使用真实 API key |
| Pytest | 9.2.0 |
| 宿主 Docker Engine | 20.10.20 |
| Compose | 2.40.3+ds1-0ubuntu1 |
| 依赖固化 | [pyproject.toml](<../../pyproject.toml>) + [uv.lock](<../../uv.lock>) |

PyPI 的 Inspect 元数据声明 Python >=3.11、MIT License；项目只承诺当前测试的 Python 3.12。应用继续使用仓库现有 Apache-2.0 许可。依赖的完整法律审核和 SBOM 发布尚未实施，不将 Inspect 的 MIT 许可等同于所有数据/依赖均可任意使用。

官方索引：`https://pypi.org/pypi/inspect-ai/0.3.290/json`，`https://inspect.aisi.org.uk/`。版本判断来自实际安装和探针，不仅依据在线文档。

## 六项 spike 的真实结果

| 探针 | 状态 | 实际证据与边界 |
|---|---|---|
| SP01 基础执行 | 通过（M1 范围） | 两题 × 两系统形成四个 Inspect 单样本日志；solver 不调用 generate；`model_usage={}` |
| SP02 沙箱文件/快照 | **未通过：环境阻塞** | Docker 显式探针在前置检查失败；Engine 20.10.20 低于包源码要求 24.0.6，且旧 CLI 的 `--format json` 返回字面量 `json`；尚未证明 Inspect 容器销毁前导出 |
| SP03 停止语义 | 本地范围通过 | Inspect async solver 的时间限制、cleanup 回调，以及控制器 deadline、取消、TERM/KILL、同进程组子进程回收和 PID 复用拒绝已测；不覆盖脱离 session 的恶意进程、容器或远程服务 |
| SP04 Bridge | 通过同进程 Chat Completions 子集 | OpenAI SDK 的三次连续对话被转发给本地 `mockllm`，日志记录三条 model event，TCP 禁用；不代表所有 SDK、并发角色、Responses 或远程 BB 调用完整性已验证 |
| SP05 离线评分 | 通过（本地） | Inspect 原生日志经 `inspect_ai.score` 重评分产生新日志，原日志哈希不变；业务快照也可独立反复评分，无 Agent 重跑 |
| SP06 中断/恢复证据 | 本地范围通过 | SQLite 事务、schema 迁移、门控启动、孤儿 worker 接管、结果发布后恢复、暂停解除和重复 resume 已测；不恢复任意 Agent 内存推理，不接管远程系统 |

测试实现见 [本地 Inspect 探针](<../../tests/test_inspect_compatibility.py>)、[Bridge 探针](<../../tests/test_inspect_bridge.py>)、[Docker 探针](<../../tests/test_inspect_docker.py>)、[中断探针](<../../tests/test_interruption.py>)。

## 核对出的接口细节

1. `Task(dataset=[Sample(...)], solver=...)` 可以配 `get_model("mockllm/model")` 执行，不调用模型也能生成真实 `.eval` 日志。
2. `eval(..., score=False, epochs=1, max_samples=1, retry_on_error=0)` 满足 M1 单 attempt/单样本与延迟评分；关闭通知、控制服务及实时日志，避免启动不需要的服务。
3. `inspect_ai.score` 是日志重评分入口；`inspect_ai.scorer.score` 是不同用途的接口，不能混用。
4. Inspect Bridge 要求 OpenAI SDK 至少 3.4.0；初次使用 2.54.0 的探针失败，修正开发依赖后锁定 3.51.0。
5. MockLLM 的 `custom_outputs` 要求 `ModelOutput`，不是字符串；不给 usage 时会尝试 tiktoken 计数，冷缓存可能下载 tokenizer。Bridge 测试显式提供模拟 `ModelUsage`，避免下载；这六个模拟 token 不记入 M1 的真实模型账本。
6. 三次互不相关的单消息请求不等价于一段连续对话，Bridge 的主输出可能保留首段；探针改为明确传递完整对话历史并核对全部事件。不能仅靠最终一条输出证明多 Agent 调用归属。
7. 私有答案没有写入执行 Sample.target，Inspect 日志 target 为空；业务 grader 通过独立可信配置加载合成答案。

## 实际验证命令

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/agent_testbed
.venv/bin/pytest -q
.venv/bin/agent-eval validate experiments/smoke-mock.yaml
.venv/bin/agent-eval run experiments/smoke-mock.yaml
TESTBED_DOCKER_PROBE=1 .venv/bin/pytest -q tests/test_inspect_docker.py
```

本轮默认测试：**54 passed，1 skipped**；skip 是显式启用的 Docker 探针。M2 本地暂停/取消/恢复、孤儿 worker 接管、结果发布后的崩溃恢复、进程组清理、状态迁移及 owner fencing 均已覆盖。worker 没有 outcome 时保留 infra_error，不自动重排队。本轮静态检查通过，未调用真实模型/BB、未执行 Git 提交推送、未核验远端 CI。

2026-11-16 单独启用 Docker 探针得到 **1 failed**，原因为上述环境不兼容；本轮未重跑该失败路径，也未升级、绕过或操作已有容器。

2026-11-16 已完成 M1 的 sdist/wheel 构建与独立安装验证。2026-11-17 在本轮代码上重新执行 `uv build`，在独立虚拟环境安装非 editable wheel 后运行四样本演示：**2 passed / 2 failed，4 个资源清理 confirmed，真实模型请求为 0**；随后 resume 不新增 attempt、不覆盖结果、不新增相同自动评分。

归档内容复核排除 `.tools`、`.venv`、运行 output 和 Git 元数据。构建工具仍提示项目内 cache 可能打包，该目录在构建配置中显式排除，并核对实际归档成员。

## 采用范围与后续门槛

允许继续的范围：受信任确定性 mock、公共文件、独立评分、本地报告及单机状态控制。M2 恢复同一 attempt，并只继续尚未派发的 run；不会自动重排队失败样本。实现和边界见 [ADR-004](<../architecture-decisions/004-m2-local-worker-state.md>)。

进入不可信代码前：获得合适的专用 Docker Engine 环境并重跑 SP02；完成 owner 资源管理、实际取消及私有材料不可达验证。进入 BB/付费实验前另做全调用归属、准入和远程幂等测试。

**没有把失败/未测标成通过，也没有为完成 M0 而改动共享基础设施。**
