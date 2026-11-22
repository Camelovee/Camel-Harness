import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { BackendProcess } from "../src/backend";

function fixture(mode = "normal"): BackendProcess {
  return new BackendProcess({
    command: process.execPath,
    args: [join(__dirname, "fixtures/backend.cjs"), mode],
    cwd: __dirname,
    frontendDist: "/中文 目录",
    startupTimeoutMs: 300,
    shutdownTimeoutMs: 300,
  });
}

describe("桌面后端进程", () => {
  it("通过管道握手并正常退出，不把凭据放进地址", async () => {
    const backend = fixture();
    try {
      const connection = await backend.start();
      expect(connection.origin).toBe("http://127.0.0.1:12345");
      expect(connection.token).toHaveLength(64);
      expect(connection.origin).not.toContain(connection.token);
    } finally { await backend.stop(); }
  });

  it.each(["hang", "invalid"])("启动 %s 时清理子进程并报告失败", async (mode) => {
    const backend = fixture(mode);
    await expect(backend.start()).rejects.toThrow();
    await backend.stop();
  });

  it("启动后崩溃通知界面，不自动重启", async () => {
    const backend = fixture("crash");
    const crashed = new Promise<void>((resolve) => backend.once("crash", resolve));
    await backend.start();
    await crashed;
    await backend.stop();
  });

  it("退出超时时结束自己拥有的进程并报告清理限制", async () => {
    const backend = fixture("stuck");
    await backend.start();
    await expect(backend.stop()).rejects.toThrow("未在 15 秒内");
  });
});
