# 固定任务评测

`tasks.json` 是版本化的 11 个小型编码任务。每项在独立临时工作区执行，按可见文本、精确文件内容、工具结果和恢复状态判分。任务覆盖读代码、写入、精确编辑、运行测试、工具失败与流中断恢复。精确文件匹配有意严格，等价但格式不同的代码可能被判失败；失败样本需要人工复核。

```bash
uv run python -m evals.run
uv run python -m evals.run --provider anthropic --model 模型ID --output report.json
uv run python -m evals.provider_smoke --provider anthropic --model 模型ID
```

默认 `fake` 运行不需要密钥，使用固定脚本返回模型事件，只证明 Harness 协议与判分器可用；不得把 11/11 解释为模型效果。真实 Provider 必须显式选择并指定模型，按运行日期、模型、任务结果、token、耗时与失败规则输出 JSON；`resume-after-failure` 是只用于离线协议测试的故障注入任务，不计入真实模型任务分母。真实任务只批准工作区文件编辑和固定测试命令，仍不是 OS 沙箱。

## 2026-10-07 基线

- Anthropic 官方 Python SDK 接入一个 Anthropic 协议兼容端点，模型 `glm-5.3-flash`；不是 Anthropic 官方模型。
- 真实文本、工具调用、取消烟测全部通过（两项任务均通过，合计 108 token；取消在首个文本增量后传播）。
- 固定真实任务 6/10 通过，详见 [`baseline-2026-10-07-anthropic-compatible.json`](baseline-2026-10-07-anthropic-compatible.json)。这是单次运行，不代表稳定成功率。
- 失败项有 `write-test`、`fix-return`、`run-test`、`repair-and-test`。详细重跑结果见 [`failures-2026-10-07-anthropic-compatible.json`](failures-2026-10-07-anthropic-compatible.json)。
- `write-test` 实际写出的 `test_addition` 在语义上满足断言，但精确文件判分只接受 `test_add`，属于判分过严；模型追加测试命令又被评测审批拒绝。
- `fix-return` 已修复源文件，但随后额外探查和被拒绝的 Shell 调用耗尽 4 Turn 预算；文件正确、Run 未完成。
- `run-test` 多次猜测项目清单文件，测试命令未获审批，最终 4 Turn 用尽；暴露任务提示过短与 Shell 白名单对模型命令变化过敏。
- `repair-and-test` 已修复代码，验证命令被拒绝后 4 Turn 用尽；失败主要来自评测授权策略，而不是编辑能力。
- 当前没有 OpenAI 密钥，因此未运行 OpenAI 真实烟测；入口已提供，不能声称通过。

优先处理失败样本，再扩大任务规模；不要把脚本化协议测试、真实模型任务效果和 Provider 兼容性混成一个数字。
