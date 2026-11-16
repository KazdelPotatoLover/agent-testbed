# Inspect M0 兼容性记录

日期：2026-11-16。**结论：Inspect 支持当前 M1 的受信任 mock 闭环；不代表所有 M0 探针或后续生产能力都已通过。**

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
| SP03 停止语义 | 部分通过 | 本地 async solver 超过 1 秒后记录 time limit，cleanup 回调执行；没有验证远程取消、子进程树或强杀资源回收 |
| SP04 Bridge | 通过同进程 Chat Completions 子集 | OpenAI SDK 的三次连续对话被转发给本地 `mockllm`，日志记录三条 model event，TCP 禁用；不代表所有 SDK、并发角色、Responses 或远程 BB 调用完整性已验证 |
| SP05 离线评分 | 通过（本地） | Inspect 原生日志经 `inspect_ai.score` 重评分产生新日志，原日志哈希不变；业务快照也可独立反复评分，无 Agent 重跑 |
| SP06 中断/恢复证据 | 部分通过 | 强制终止本地 worker 后矩阵和目录保留，status 显示 incomplete，不自动重启；中间推理恢复、远程重连和 SQLite 恢复尚属 M2 |

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

本轮默认测试：**32 passed，1 skipped**；skip 是需要显式启用的 Docker 探针。单独启用 Docker 后得到 **1 failed**，原因为上述前置环境不兼容，未绕过、未升级宿主，也未操作既有容器。静态检查通过；远端 CI 未运行。

另外已实际执行 `uv build`，生成 sdist 和 wheel，检查归档内容未包含 `.tools`、`.venv`、运行 output 或 Git 元数据；在新的项目内虚拟环境中按锁定哈希安装运行依赖，再安装非 editable wheel，重新执行四样本 mock 闭环成功。构建工具提示项目内 cache 可能打包，归档清单复核确认未被包含，构建配置也显式排除了这些目录。

## 采用范围与后续门槛

允许继续的范围：受信任确定性 mock、纯文件输入输出、独立规则评分和本地报告。M1 不接受未知适配器或真实模型配置。

进入 M2/不可信代码前：获得合适的专用 Docker Engine 环境并重跑 SP02；完成状态持久化、owner 资源管理、实际取消及私有材料不可达验证。进入 BB/付费实验前另做全调用归属、准入和远程幂等测试。

**没有把失败/未测标成通过，也没有为完成 M0 而改动共享基础设施。**
