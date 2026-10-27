import { app, BrowserWindow, dialog, ipcMain, Menu, session, shell, type MenuItemConstructorOptions } from "electron";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { BackendProcess, type BackendConnection } from "./backend";

let mainWindow: BrowserWindow | undefined;
let backend: BackendProcess | undefined;
let connection: BackendConnection | undefined;
let starting = false;
let quitting = false;
let quitPending = false;
const statusPath = join(app.getAppPath(), "assets", "status.html");

function isTrustedFrame(event: Electron.IpcMainInvokeEvent, allowStatus = false): boolean {
  if (!mainWindow || event.sender !== mainWindow.webContents ||
      event.senderFrame !== mainWindow.webContents.mainFrame) return false;
  const url = new URL(event.senderFrame.url);
  return (connection !== undefined && url.origin === connection.origin) ||
    (allowStatus && url.protocol === "file:" && url.pathname === new URL(pathToFileURL(statusPath).href).pathname);
}

async function openExternal(url: string): Promise<void> {
  try {
    if (["https:", "http:"].includes(new URL(url).protocol)) await shell.openExternal(url);
  } catch { /* 非网页链接不交给系统处理。 */ }
}

async function showStatus(state: "starting" | "failed"): Promise<void> {
  await mainWindow?.loadFile(statusPath, { query: { state } });
  mainWindow?.show();
}

async function startBackend(): Promise<void> {
  if (starting || quitting) return;
  starting = true;
  connection = undefined;
  await showStatus("starting");
  const desktopRoot = app.getAppPath();
  const ownedBackend = new BackendProcess({
    command: app.isPackaged
      ? join(process.resourcesPath, "backend", process.platform === "win32" ? "camel-backend.exe" : "camel-backend")
      : "uv",
    args: app.isPackaged ? [] : ["run", "--no-sync", "python", "-m", "camel_backend.desktop"],
    cwd: app.isPackaged ? app.getPath("home") : join(desktopRoot, ".."),
    frontendDist: app.isPackaged ? join(process.resourcesPath, "webui") : join(desktopRoot, "..", "webui", "dist"),
  });
  backend = ownedBackend;
  ownedBackend.on("crash", () => {
    if (backend === ownedBackend && !quitting) {
      connection = undefined;
      void showStatus("failed");
    }
  });
  try {
    const readyConnection = await ownedBackend.start();
    if (quitting) return;
    connection = readyConnection;
    const response = await fetch(`${connection.origin}/api/health`, {
      headers: { "X-Camel-Desktop-Token": connection.token },
      signal: AbortSignal.timeout(3000), redirect: "error",
    });
    if (!response.ok) throw new Error("本地服务未就绪");
    await mainWindow?.loadURL(connection.origin);
    mainWindow?.show();
  } catch {
    connection = undefined;
    await ownedBackend.stop().catch(() => undefined);
    if (!quitting) await showStatus("failed");
  } finally {
    starting = false;
  }
}

async function requestQuit(): Promise<void> {
  if (quitPending || quitting) return;
  quitPending = true;
  try {
    let activeRuns: number | null = 0;
    if (connection) {
      try {
        const response = await fetch(`${connection.origin}/api/desktop/status`, {
          headers: { "X-Camel-Desktop-Token": connection.token },
          signal: AbortSignal.timeout(2000), redirect: "error",
        });
        if (!response.ok) throw new Error("无法查询任务");
        const status = await response.json() as { active_runs: number };
        activeRuns = status.active_runs;
      } catch { activeRuns = null; }
    }
    if (activeRuns === null || activeRuns > 0) {
      const result = await dialog.showMessageBox(mainWindow!, {
        type: "question", buttons: ["继续运行", "停止并退出"], defaultId: 0, cancelId: 0,
        message: activeRuns === null ? "暂时无法确认任务状态，仍要退出吗？" : `还有 ${activeRuns} 个任务正在运行，要停止并退出吗？`,
        detail: "退出会中断任务，已经完成的文件修改和命令操作不会撤销。",
      });
      if (result.response === 0) return;
    }
    quitting = true;
    mainWindow?.setEnabled(false);
    try { await backend?.stop(); } catch {
      await dialog.showMessageBox({ type: "warning", message: "后端未在 15 秒内完成清理，已请求强制结束。", detail: "下次启动前请核验正在修改的文件和命令结果。" });
    }
    app.quit();
  } finally { quitPending = false; }
}

function setupMenu(): void {
  const action = (name: string) => () => mainWindow?.webContents.send("desktop:action", name);
  const template: MenuItemConstructorOptions[] = [
    ...(process.platform === "darwin" ? [{ role: "appMenu" as const }] : []),
    { label: "文件", submenu: [
      { label: "新建对话", accelerator: "CommandOrControl+N", click: action("new-chat") },
      { label: "打开项目…", accelerator: "CommandOrControl+O", click: action("open-project") },
      { label: "设置…", accelerator: "CommandOrControl+,", click: action("settings") },
      { type: "separator" }, { label: "退出", accelerator: "CommandOrControl+Q", click: () => { void requestQuit(); } },
    ] },
    { role: "editMenu", label: "编辑" },
    { label: "视图", submenu: [
      { label: "切换侧栏", accelerator: "CommandOrControl+B", click: action("toggle-sidebar") },
      { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { role: "togglefullscreen" },
    ] },
    { label: "帮助", submenu: [
      { label: "项目与下载", click: () => { void openExternal("https://github.com/Camelovee/Camel-Harness/releases"); } },
      { label: "关于 Camel Harness", click: () => { void dialog.showMessageBox({ message: `Camel Harness ${app.getVersion()}`, detail: "本地编程助手 · 未签名预览版" }); } },
    ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow?.isMinimized()) mainWindow.restore();
    mainWindow?.show(); mainWindow?.focus();
  });
  app.on("before-quit", (event) => { if (!quitting) { event.preventDefault(); void requestQuit(); } });
  void app.whenReady().then(async () => {
    const privateSession = session.fromPartition("camel-desktop");
    privateSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    privateSession.webRequest.onBeforeSendHeaders({ urls: ["<all_urls>"] }, (details, callback) => {
      const headers = details.requestHeaders;
      for (const name of Object.keys(headers)) {
        if (name.toLowerCase() === "x-camel-desktop-token") delete headers[name];
      }
      if (connection && details.webContentsId === mainWindow?.webContents.id &&
          new URL(details.url).origin === connection.origin &&
          new URL(details.url).pathname.startsWith("/api/") &&
          (!details.frame || details.frame === mainWindow?.webContents.mainFrame)) {
        headers["X-Camel-Desktop-Token"] = connection.token;
      }
      callback({ requestHeaders: headers });
    });
    mainWindow = new BrowserWindow({
      title: "Camel Harness", width: 1280, height: 840, minWidth: 960, minHeight: 640,
      show: false, backgroundColor: "#f7f7f5",
      webPreferences: {
        preload: join(__dirname, "preload.js"), session: privateSession,
        contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true,
      },
    });
    mainWindow.on("close", (event) => { if (!quitting) { event.preventDefault(); void requestQuit(); } });
    mainWindow.webContents.on("will-navigate", (event, url) => {
      if (!connection || new URL(url).origin !== connection.origin) {
        event.preventDefault(); void openExternal(url);
      }
    });
    mainWindow.webContents.setWindowOpenHandler(({ url }) => { void openExternal(url); return { action: "deny" }; });
    ipcMain.handle("desktop:select-directory", async (event) => {
      if (!isTrustedFrame(event)) throw new Error("无法验证窗口来源");
      const result = await dialog.showOpenDialog(mainWindow!, { title: "选择 Camel Harness 工作区", properties: ["openDirectory"] });
      return result.canceled ? null : result.filePaths[0] ?? null;
    });
    ipcMain.handle("desktop:app-info", (event) => {
      if (!isTrustedFrame(event, true)) throw new Error("无法验证窗口来源");
      return { version: app.getVersion(), platform: process.platform };
    });
    ipcMain.handle("desktop:retry", async (event) => {
      if (!isTrustedFrame(event, true) || connection) throw new Error("无法重新启动后端");
      await startBackend();
    });
    setupMenu();
    await startBackend();
  });
}
