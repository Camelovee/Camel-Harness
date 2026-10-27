const { _electron: electron } = require("playwright");
const { mkdtemp, mkdir, writeFile, readFile, readdir, realpath, rm } = require("node:fs/promises");
const { createServer } = require("node:http");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");
const assert = require("node:assert/strict");

async function main() {
  const home = await realpath(await mkdtemp(join(tmpdir(), "camel-desktop-")));
  const desktopRoot = resolve(__dirname, "..");
  const project = join(home, "中文 项目");
  await mkdir(project);
  let modelRequests = 0;
  const modelServer = createServer((request, response) => {
    request.resume();
    request.on("end", () => {
      response.writeHead(200, { "content-type": "text/event-stream" });
      const event = (type, payload) => response.write(`event: ${type}\ndata: ${JSON.stringify({ type, ...payload })}\n\n`);
      if (request.url.endsWith("/responses")) {
        const result = { id: "response-smoke", model: "desktop-openai-test", output: [], status: "completed", usage: { input_tokens: 10, output_tokens: 10 } };
        event("response.created", { sequence_number: 0, response: { ...result, status: "in_progress" } });
        event("response.output_text.delta", { sequence_number: 1, item_id: "message-smoke", output_index: 0, content_index: 0, delta: "OpenAI 离线验收完成" });
        event("response.completed", { sequence_number: 2, response: result });
        response.end();
        return;
      }
      const requestIndex = modelRequests++;
      event("message_start", { message: { id: `msg-${requestIndex}`, type: "message", role: "assistant", model: "desktop-test", content: [], stop_reason: null, usage: { input_tokens: 10, output_tokens: 0 } } });
      if (requestIndex === 0) {
        event("content_block_start", { index: 0, content_block: { type: "tool_use", id: "smoke-write", name: "write_file", input: {} } });
        event("content_block_delta", { index: 0, delta: { type: "input_json_delta", partial_json: JSON.stringify({ path: join(project, "hello.txt"), content: "桌面离线验收完成" }) } });
      } else {
        event("content_block_start", { index: 0, content_block: { type: "text", text: "" } });
        event("content_block_delta", { index: 0, delta: { type: "text_delta", text: requestIndex === 1 ? "桌面离线验收完成" : "尚未完成的测试响应" } });
      }
      if (requestIndex > 1) return;
      event("content_block_stop", { index: 0 });
      event("message_delta", { delta: { stop_reason: requestIndex === 0 ? "tool_use" : "end_turn", stop_sequence: null }, usage: { output_tokens: 10 } });
      event("message_stop", {});
      response.end();
    });
  });
  await new Promise((resolveListen) => modelServer.listen(0, "127.0.0.1", resolveListen));
  const modelBaseUrl = `http://127.0.0.1:${modelServer.address().port}`;
  await mkdir(join(home, ".camel"));
  // 故意破坏临时配置，验证故障页和人工重启，不接触真实模型或用户资料。
  await writeFile(join(home, ".camel/config.json"), "invalid json");
  let application;
  try {
    const launchEnvironment = { ...process.env, HOME: home, USERPROFILE: home };
    if (process.argv[2]) {
      for (const name of Object.keys(launchEnvironment)) {
        if (name.toLowerCase() === "path") delete launchEnvironment[name];
      }
      // 安装包验收不允许借用本机开发用的 Python、Node 或 uv。
      launchEnvironment.PATH = process.platform === "win32"
        ? join(process.env.SystemRoot || "C:\\Windows", "System32") : "/usr/bin:/bin:/usr/sbin:/sbin";
    }
    application = await electron.launch({
      ...(process.argv[2] ? { executablePath: resolve(process.argv[2]) } : {}),
      args: [`--user-data-dir=${join(home, "profile")}`, ...(process.argv[2] ? [] : [desktopRoot])],
      cwd: resolve(desktopRoot, ".."),
      env: launchEnvironment,
      timeout: 45_000,
    });
    const page = await application.firstWindow();
    await page.getByRole("button", { name: "重新启动后端" }).waitFor({ state: "visible", timeout: 45_000 });
    await writeFile(join(home, ".camel/config.json"), "{}");
    await page.getByRole("button", { name: "重新启动后端" }).click();
    await page.waitForURL("http://127.0.0.1:*/", { timeout: 45_000 });
    await page.getByRole("button", { name: "关闭设置" }).waitFor();
    assert.equal(await page.evaluate(() => typeof window.require), "undefined");
    const info = await page.evaluate(() => window.camelDesktop.appInfo());
    assert.ok(info.version);
    const unauthorized = await fetch(`${new URL(page.url()).origin}/api/health`);
    assert.equal(unauthorized.status, 401);
    await application.evaluate(({ dialog }, projectPath) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [projectPath] });
    }, project);
    assert.equal(await page.evaluate(() => window.camelDesktop.selectDirectory()), project);
    const createdModel = await page.evaluate(async (baseUrl) => {
      const response = await fetch("/api/settings/models", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "离线验收模型", provider: "anthropic", model: "desktop-test", base_url: baseUrl, api_key: "local-test-only", thinking_mode: "provider_default" }),
      });
      return response.status;
    }, modelBaseUrl);
    assert.equal(createdModel, 201);
    await page.reload();
    await page.getByRole("button", { name: "选择本地工作区", exact: false }).click();
    await page.waitForFunction((projectPath) => document.querySelector(".main-header-sub")?.textContent === projectPath, project);
    await page.getByRole("textbox").fill("在临时项目中写入验收文件");
    await page.getByRole("textbox").press("Enter");
    await page.getByRole("button", { name: "允许执行" }).click({ timeout: 30_000 });
    await page.locator(".markdown-body").getByText("桌面离线验收完成").waitFor();
    await page.getByRole("button", { name: "暂停当前 Run" }).waitFor({ state: "hidden" });
    assert.equal(await readFile(join(project, "hello.txt"), "utf8"), "桌面离线验收完成");
    await page.locator(".tool-message > summary").click();
    await page.getByRole("button", { name: "在侧栏查看" }).click();
    await page.getByRole("complementary", { name: "工具详情" }).waitFor();
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(960, 640));
    await page.getByRole("complementary", { name: "工具详情" }).waitFor({ state: "hidden" });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    await application.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0].setSize(1280, 840);
      BrowserWindow.getAllWindows()[0].webContents.send("desktop:action", "toggle-sidebar");
    });
    await page.getByRole("button", { name: "展开侧栏" }).waitFor();
    await page.getByRole("button", { name: "展开侧栏" }).click();
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("button", { name: "外观", exact: true }).click();
    await page.getByLabel("界面主题").selectOption("dark");
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    await page.getByRole("button", { name: "关闭设置" }).click();
    if (process.argv[3]) await page.screenshot({ path: resolve(process.argv[3]).replace(/\.png$/, "-dark.png"), fullPage: true });
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("button", { name: "外观", exact: true }).click();
    await page.getByLabel("界面主题").selectOption("light");
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    await page.getByRole("button", { name: "关闭设置" }).click();
    if (process.argv[3]) await page.screenshot({ path: resolve(process.argv[3]), fullPage: true });
    const providerModels = await page.evaluate(async (baseUrl) => {
      const response = await fetch("/api/settings/models", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "OpenAI 离线模型", provider: "openai", model: "desktop-openai-test", base_url: `${baseUrl}/v1`, api_key: "local-test-only", thinking_mode: "provider_default" }),
      });
      if (!response.ok) throw new Error("新增离线模型失败");
      return (await response.json()).models;
    }, modelBaseUrl);
    await page.reload();
    await page.getByLabel("选择模型").selectOption(providerModels.find((model) => model.provider === "openai").id);
    await page.getByRole("textbox").fill("验证 OpenAI 官方 SDK 打包");
    await page.getByRole("textbox").press("Enter");
    await page.getByText("OpenAI 离线验收完成", { exact: true }).waitFor();
    await page.getByRole("button", { name: "暂停当前 Run" }).waitFor({ state: "hidden" });
    await page.getByLabel("选择模型").selectOption(providerModels.find((model) => model.provider === "anthropic").id);
    await page.waitForFunction(() => !document.querySelector(".model-select").disabled);
    await page.getByRole("textbox").fill("保持运行，验证退出取消和清理");
    await page.getByRole("textbox").press("Enter");
    await page.getByText("尚未完成的测试响应", { exact: true }).waitFor();
    // 使用真实活动 Run，只替换系统确认框，取消退出后任务应继续运行。
    await application.evaluate(({ dialog, BrowserWindow }) => {
      dialog.showMessageBox = async () => ({ response: 0, checkboxChecked: false });
      BrowserWindow.getAllWindows()[0].close();
    });
    await application.evaluate(async ({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows()[0];
      for (let attempt = 0; attempt < 100 && !window.isEnabled(); attempt++) await new Promise((wait) => setTimeout(wait, 50));
      if (!window.isEnabled()) throw new Error("取消退出后窗口未恢复");
    });
    assert.equal(application.windows().length, 1);
    await page.getByRole("button", { name: "暂停当前 Run" }).waitFor();
    console.log("桌面验收通过：故障重启、隔离窗口、鉴权、原生目录、双厂商 SDK 本机流、审批写入、主题、详情缩放与取消退出。");
    const exited = new Promise((resolveExit) => application.on("close", resolveExit));
    await application.evaluate(({ dialog, BrowserWindow }) => {
      dialog.showMessageBox = async () => ({ response: 1, checkboxChecked: false });
      BrowserWindow.getAllWindows()[0].close();
    });
    await Promise.race([exited, new Promise((_, reject) => setTimeout(() => reject(new Error("退出超时")), 20_000).unref())]);
    application = undefined;
    const sessionRoot = join(home, ".camel", "sessions");
    const sessionFiles = (await readdir(sessionRoot, { recursive: true })).filter((name) => name.endsWith(".jsonl"));
    const savedHistory = (await Promise.all(sessionFiles.map((name) => readFile(join(sessionRoot, name), "utf8")))).join("\n");
    assert.ok(savedHistory.includes("桌面离线验收完成"));
    assert.ok(!savedHistory.includes("尚未完成的测试响应"));
    console.log("运行中退出通过：任务已取消，后端正常清理。");
  } finally {
    if (application) await application.close();
    modelServer.closeAllConnections();
    await new Promise((resolveClose) => modelServer.close(resolveClose));
    await rm(home, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
