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
      configured: true,
      has_api_key: true,
    },
  ],
  default_model_id: "m1",
  thinking: false,
};

beforeEach(() => {
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
