# Camel Harness WebUI

React/Vite 前端，默认通过 Vite proxy 访问 `http://127.0.0.1:8000` 的 FastAPI。

```bash
npm install
npm run dev
```

设计重点：参考 DeepSeek Harness Java 控制台的工作区侧栏、对话/轨迹双视图、Markdown/工具卡片语义、POST + SSE 流式对话、浏览器审批队列和权限模式切换。生产化阶段再补充文件树、差异审阅、任务时间线和多用户鉴权。
