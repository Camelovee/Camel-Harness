const { _electron: electron } = require("playwright");
const { mkdtemp, mkdir, writeFile, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");
const assert = require("node:assert/strict");

async function main() {
  const home = await mkdtemp(join(tmpdir(), "camel-desktop-"));
  const desktopRoot = resolve(__dirname, "..");
  await mkdir(join(home, ".camel"));
  // 故意破坏临时配置，验证故障页和人工重启，不接触真实模型或用户资料。
  await writeFile(join(home, ".camel/config.json"), "invalid json");
  let application;
  try {
    application = await electron.launch({
      ...(process.argv[2] ? { executablePath: resolve(process.argv[2]) } : {}),
      args: [`--user-data-dir=${join(home, "profile")}`, ...(process.argv[2] ? [] : [desktopRoot])],
      cwd: resolve(desktopRoot, ".."),
      env: { ...process.env, HOME: home, USERPROFILE: home },
      timeout: 45_000,
    });
    const page = await application.firstWindow();
    await page.getByRole("button", { name: "重新启动后端" }).waitFor({ state: "visible", timeout: 45_000 });
    await writeFile(join(home, ".camel/config.json"), "{}");
    await page.getByRole("button", { name: "重新启动后端" }).click();
    await page.waitForURL("http://127.0.0.1:*/", { timeout: 45_000 });
    await page.getByRole("button", { name: "设置", exact: true }).waitFor();
    assert.equal(await page.evaluate(() => typeof window.require), "undefined");
    const info = await page.evaluate(() => window.camelDesktop.appInfo());
    assert.ok(info.version);
    const unauthorized = await fetch(`${new URL(page.url()).origin}/api/health`);
    assert.equal(unauthorized.status, 401);
    await application.evaluate(({ dialog }) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: ["/中文 项目"] });
    });
    assert.equal(await page.evaluate(() => window.camelDesktop.selectDirectory()), "/中文 项目");
    // 主进程的退出分支使用受控状态替身，真正的鉴权与关闭由后端进程测试覆盖。
    await application.evaluate(({ dialog, BrowserWindow }) => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = (url, init) => String(url).endsWith("/api/desktop/exit") && init?.method === "POST"
        ? Promise.resolve(new Response('{"active_runs":1}')) : originalFetch(url, init);
      dialog.showMessageBox = async () => ({ response: 0, checkboxChecked: false });
      BrowserWindow.getAllWindows()[0].close();
    });
    await page.waitForFunction(() => document.readyState === "complete");
    assert.equal(application.windows().length, 1);
    if (process.argv[3]) {
      await page.screenshot({ path: resolve(process.argv[3]), fullPage: true });
    }
    console.log("桌面验收通过：启动失败、人工重启、隔离窗口、API 鉴权、原生目录和取消退出。");
    const exited = new Promise((resolveExit) => application.on("close", resolveExit));
    await application.evaluate(({ dialog, BrowserWindow }) => {
      dialog.showMessageBox = async () => ({ response: 1, checkboxChecked: false });
      BrowserWindow.getAllWindows()[0].close();
    });
    await Promise.race([exited, new Promise((_, reject) => setTimeout(() => reject(new Error("退出超时")), 20_000).unref())]);
    application = undefined;
  } finally {
    if (application) await application.close();
    await rm(home, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
