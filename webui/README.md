# Camel Harness WebUI

React/Vite 前端，默认通过 Vite proxy 访问 `http://127.0.0.1:8000` 的 FastAPI。

先按 [根目录 README](../README.md#后端启动) 启动后端，再进入 `webui` 目录执行：

```bash
npm ci
npm run dev
```

当前提供工作区/会话侧栏、Markdown 消息与内联工具/审批卡片、POST + SSE 流式对话、取消与恢复、权限模式和模型设置；不设置独立轨迹页。

界面与状态边界见 [WebUI 设计](../docs/WebUI设计.md)，后续功能及优先级统一见 [路书](../docs/路书.md)。本项目面向单机本地使用，不提供多用户鉴权或持久化后台任务。
