# 变更记录

## 0.1.0 — 待发布

- Python Harness Agent：流式多 Turn ReAct、生命周期事件、steering、取消与恢复。
- Anthropic 和 OpenAI 官方 SDK 适配，支持工具调用、思考增量与用量事件。
- 四个核心工具、权限审批、MCP、Skill 渐进式加载和子 Agent 委派。
- 本地 JSONL 会话、父子会话目录、上下文压缩及 WebUI/CLI 入口。
- 新增锁定依赖的 CI、11 项离线评测、真实 Provider 可选烟测与任务报告。
- CLI `--latest-session` 与 REPL `/resume` 语义分离，缺失会话消息接口返回 404。
- 提供旧平铺 JSONL 的预览式复制迁移脚本；项目采用 MIT 许可证。

版本尚未打标签；已知限制和验证方式见 [README](README.md) 与 [路书](docs/路书.md)。
