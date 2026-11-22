// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { SettingsDialog } from "../../renderer/src/features/settings/SettingsDialog";
import type { WebSettings } from "../../renderer/src/shared/model";

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
  theme: "system",
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
      onSavePreferences={async () => true} onSaveTheme={async () => true}
      onSaveModel={async () => true}
      onDeleteModel={async () => true}
    />,
  );
}

it("默认展示模型面板并可切换到预留面板", () => {
  renderDialog();
  expect(screen.getByLabelText("默认模型")).toBeTruthy();
  expect(screen.queryByText(/此面板为预留入口/)).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Skills" }));
  expect(screen.getByText(/SKILL.md 约定发现/)).toBeTruthy();
  expect(screen.queryByLabelText("默认模型")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "MCP" }));
  expect(screen.getByText(/\.mcp\.json/)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "模型" }));
  expect(screen.getByLabelText("默认模型")).toBeTruthy();
});


it("模型预算与思考模式可编辑并随请求保存", async () => {
  const save = vi.fn(async () => true);
  render(<SettingsDialog settings={settings} saving={false} onClose={() => undefined}
    onSavePreferences={async () => true} onSaveTheme={async () => true} onSaveModel={save} onDeleteModel={async () => true} />);
  fireEvent.click(screen.getByRole("button", { name: "编辑模型 测试模型" }));
  fireEvent.click(screen.getByText("高级参数"));
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

it("切换服务商时重置思考参数，新增模型有明确默认预算", () => {
  renderDialog();
  fireEvent.click(screen.getByRole("button", { name: /添加模型/ }));
  fireEvent.click(screen.getByText("高级参数"));
  expect((screen.getByLabelText("上下文窗口 Token") as HTMLInputElement).value).toBe("128000");
  expect((screen.getByLabelText("最大输出 Token") as HTMLInputElement).value).toBe("16384");
  fireEvent.change(screen.getByLabelText("思考参数模式"), { target: { value: "anthropic_adaptive" } });
  fireEvent.change(screen.getByLabelText("服务商"), { target: { value: "openai" } });
  expect((screen.getByLabelText("思考参数模式") as HTMLSelectElement).value).toBe("provider_default");
  expect(screen.queryByRole("option", { name: "Anthropic adaptive" })).toBeNull();
  expect(screen.getByRole("option", { name: "OpenAI reasoning" })).toBeTruthy();
});

it("高级参数默认折叠，直接保存仍保留现有模型预算与接口地址", () => {
  const saveModel = vi.fn(async () => true);
  render(<SettingsDialog settings={settings} saving={false} onClose={() => undefined}
    onSavePreferences={async () => true} onSaveTheme={async () => true}
    onSaveModel={saveModel} onDeleteModel={async () => true} />);
  fireEvent.click(screen.getByRole("button", { name: "编辑模型 测试模型" }));
  const advanced = screen.getByText("高级参数").closest("details")!;
  expect(advanced.open).toBe(false);
  expect(document.activeElement).toBe(screen.getByLabelText("显示名称"));
  fireEvent.click(screen.getByRole("button", { name: "保存模型" }));
  expect(saveModel).toHaveBeenCalledWith("m1", expect.objectContaining({
    context_window_tokens: 262144, max_output_tokens: 16384,
    thinking_mode: "provider_default", base_url: "", api_key: "",
  }));
});

it("折叠参数校验失败时展开对应字段，允许用户修正", () => {
  renderDialog();
  fireEvent.click(screen.getByRole("button", { name: "编辑模型 测试模型" }));
  const advanced = screen.getByText("高级参数").closest("details")!;
  fireEvent.invalid(screen.getByLabelText("上下文窗口 Token"));
  expect(advanced.open).toBe(true);
});

it("删除模型先展示后果并支持取消，确认后才提交删除", () => {
  const deleteModel = vi.fn(async () => true);
  render(<SettingsDialog settings={settings} saving={false} onClose={() => undefined}
    onSavePreferences={async () => true} onSaveTheme={async () => true}
    onSaveModel={async () => true} onDeleteModel={deleteModel} />);
  fireEvent.click(screen.getByRole("button", { name: "删除模型 测试模型" }));
  expect(screen.getByText(/相关会话将切换到默认模型/)).toBeTruthy();
  expect(deleteModel).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("button", { name: "确认删除" })).toBeNull();
  expect(deleteModel).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "删除模型 测试模型" }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  expect(deleteModel).toHaveBeenCalledWith("m1");
});

it("外观设置保存主题，不覆盖默认模型", () => {
  const saveTheme = vi.fn(async () => true);
  render(<SettingsDialog settings={settings} saving={false} onClose={() => undefined}
    onSavePreferences={async () => true} onSaveTheme={saveTheme}
    onSaveModel={async () => true} onDeleteModel={async () => true} />);
  fireEvent.click(screen.getByRole("button", { name: "外观" }));
  fireEvent.change(screen.getByLabelText("界面主题"), { target: { value: "dark" } });
  expect(saveTheme).toHaveBeenCalledWith("dark");
});
