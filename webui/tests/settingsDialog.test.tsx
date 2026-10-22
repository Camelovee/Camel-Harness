// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { SettingsDialog } from "../src/features/settings/SettingsDialog";
import type { WebSettings } from "../src/shared/model";

const settings: WebSettings = {
  models: [
    {
      id: "m1",
      name: "测试模型",
      provider: "anthropic",
      model: "claude-test",
      base_url: null,
      context_window_tokens: 262144,
      max_output_tokens: 16384,
      thinking_mode: "provider_default",
      configured: true,
      has_api_key: true,
    },
  ],
  default_model_id: "m1",
};

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderDialog() {
  return render(
    <SettingsDialog
      settings={settings}
      saving={false}
      onClose={() => undefined}
      onSavePreferences={async () => true}
      onSaveModel={async () => true}
      onDeleteModel={async () => true}
    />,
  );
}

it("默认展示模型面板并可切换到预留面板", () => {
  renderDialog();
  expect(screen.getByText("对话偏好")).toBeTruthy();
  expect(screen.queryByText(/此面板为预留入口/)).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Skills" }));
  expect(screen.getByText(/SKILL.md 约定发现/)).toBeTruthy();
  expect(screen.queryByText("对话偏好")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "MCP" }));
  expect(screen.getByText(/\.mcp\.json/)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "模型" }));
  expect(screen.getByText("对话偏好")).toBeTruthy();
});


it("模型预算与思考模式可编辑并随请求保存", async () => {
  const save = vi.fn(async () => true);
  render(<SettingsDialog settings={settings} saving={false} onClose={() => undefined}
    onSavePreferences={async () => true} onSaveModel={save} onDeleteModel={async () => true} />);
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
  const windowInput = screen.getByLabelText("上下文窗口 Token") as HTMLInputElement;
  expect(windowInput.value).toBe("262144");
  fireEvent.change(windowInput, { target: { value: "128000" } });
  fireEvent.change(screen.getByLabelText("最大输出 Token"), { target: { value: "8192" } });
  fireEvent.change(screen.getByLabelText("思考参数模式"), { target: { value: "anthropic_adaptive" } });
  fireEvent.click(screen.getByRole("button", { name: "保存模型" }));
  expect(save).toHaveBeenCalledWith("m1", expect.objectContaining({
    context_window_tokens: 128000, max_output_tokens: 8192, thinking_mode: "anthropic_adaptive",
    api_key: "",
  }));
});

it("切换 Provider 时重置思考参数，新增模型有明确默认预算", () => {
  renderDialog();
  fireEvent.click(screen.getByRole("button", { name: /添加模型/ }));
  expect((screen.getByLabelText("上下文窗口 Token") as HTMLInputElement).value).toBe("128000");
  expect((screen.getByLabelText("最大输出 Token") as HTMLInputElement).value).toBe("16384");
  fireEvent.change(screen.getByLabelText("思考参数模式"), { target: { value: "anthropic_adaptive" } });
  fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "openai" } });
  expect((screen.getByLabelText("思考参数模式") as HTMLSelectElement).value).toBe("provider_default");
  expect(screen.queryByRole("option", { name: "Anthropic adaptive" })).toBeNull();
  expect(screen.getByRole("option", { name: "OpenAI reasoning" })).toBeTruthy();
});
