# ADR-002：公共输入、独立评分与当前信任边界

- 日期：2026-11-16
- 状态：M1 接受；**不代表生产安全隔离完成**

公共 `TaskSpec` 使用严格字段，包含输入 manifest、输出 schema 和任务说明，不允许 expected_answer。执行阶段只 stage 公共文件，Sample.target 保持为空。GradingSpec 与合成答案在独立目录，评分发生于输出冻结后。

快照只允许非越界、非符号链接的普通文件，采用禁止覆盖写入、SHA-256 和大小复核。缺失/错误交付记 failed，产物篡改或评分 fixture 错误记 grader_error，答错不重跑。评分批次追加保存，重评分不修改原始执行日志。

**限制**：M1 的 mock 在同进程执行；M2 已迁移到独立 worker，只传公共任务和 staged 输入，不加载 GradingSpec。两者仍使用同一 OS 用户，恶意代码仍可能读取宿主文件；数据契约和进程分离不构成安全沙箱。

M2 的容器/OS 安全隔离验收仍待专用环境完成。不得将真实隐藏答案、公司材料或不可信代码放入当前工作流。详见 [兼容性记录](<../compatibility/inspect-m0.md>) 和 [ADR-004](<004-m2-local-worker-state.md>)。
