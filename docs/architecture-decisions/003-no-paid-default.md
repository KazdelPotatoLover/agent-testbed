# ADR-003：M1 零真实调用与预算声明

- 日期：2026-11-16
- 状态：M1 接受；真实预算准入推迟到 M4

M1 配置只接受 `mode: mock`、`model_profile: null`、`real_model_requests: 0`、`experiment_total_tokens: 0`、并发 1 和重试 0。未知系统、真实模型、非零预算或隐式类型转换均校验失败，不采用“降级为别的模型”策略。

运行使用本地 MockLLM 作为 Inspect 上下文，但业务 solver 不调用 generate；真实调用和模型 token 为零的依据是空 model_usage 与 TCP 禁用测试，而不是将缺失用量填零。执行失败且可观测结果不完整时，usage 为 null，报告明确缺失。

Bridge 兼容性测试有三次本地模拟生成，显式赋予六个模拟 token 用于测试 SDK 转发；它与业务 demo 的零真实调用账本分开，不将模拟计量当作真实消耗。

Python socket guard 只是受信任代码的防意外网络调用措施，不是恶意代码隔离或全系统网络审计。没有网关、原子预留、价格表或提供方凭据，故不宣称 controlled/hard-bound 预算能力。

引入真实模型前必须单独完成调用归属、所有角色覆盖、SDK 重试、预留/结算、在途未知用量和费用授权；不存在已授权的收费默认配置。
