import { spawn, execFile, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomBytes } from "node:crypto";
import { EventEmitter } from "node:events";

export type BackendConnection = { origin: string; token: string };
export type BackendOptions = {
  command: string;
  args: string[];
  cwd: string;
  frontendDist: string;
  startupTimeoutMs?: number;
  shutdownTimeoutMs?: number;
};

export class BackendProcess extends EventEmitter<{ crash: [] }> {
  private child?: ChildProcessWithoutNullStreams;
  private stopping = false;
  private exited?: Promise<void>;

  constructor(private readonly backendOptions: BackendOptions) {
    super();
  }

  async start(): Promise<BackendConnection> {
    if (this.child) throw new Error("本地后端已经启动");
    const token = randomBytes(32).toString("hex");
    const child = spawn(this.backendOptions.command, this.backendOptions.args, {
      cwd: this.backendOptions.cwd,
      stdio: "pipe",
      windowsHide: true,
      detached: process.platform !== "win32",
    });
    this.child = child;
    this.exited = new Promise((resolve) => child.once("close", () => resolve()));
    // 不记录 stdout/stderr，避免模型配置、工具输出或用户正文进入桌面日志。
    child.stderr.resume();
    const connection = await new Promise<BackendConnection>((resolve, reject) => {
      let ready = false;
      let buffer = "";
      const timer = setTimeout(() => {
        reject(new Error("本地后端启动超过 30 秒，请检查配置后重试"));
      }, this.backendOptions.startupTimeoutMs ?? 30_000);
      const fail = () => {
        clearTimeout(timer);
        reject(new Error("本地后端启动失败，请检查配置或重新安装应用"));
      };
      child.once("error", fail);
      child.stdin.on("error", () => { if (!ready) fail(); });
      child.once("close", () => {
        clearTimeout(timer);
        if (!ready) fail();
        else if (!this.stopping) this.emit("crash");
      });
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk: string) => {
        if (ready) return;
        buffer += chunk;
        if (buffer.length > 8192) { fail(); return; }
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          try {
            const message: unknown = JSON.parse(line);
            if (typeof message !== "object" || message === null || !("type" in message)) {
              fail(); return;
            }
            if (message.type === "error") { fail(); return; }
            if (message.type !== "ready" || !("port" in message) ||
                typeof message.port !== "number" || !Number.isInteger(message.port) ||
                message.port < 1 || message.port > 65535) { fail(); return; }
            ready = true;
            clearTimeout(timer);
            resolve({ origin: `http://127.0.0.1:${message.port}`, token });
          } catch { fail(); return; }
        }
      });
      child.stdin.write(`${JSON.stringify({ token, frontend_dist: this.backendOptions.frontendDist })}\n`);
    }).catch(async (error: unknown) => {
      await this.stop().catch(() => undefined);
      throw error;
    });
    return connection;
  }

  async stop(): Promise<void> {
    this.stopping = true;
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    child.stdin.end(`${JSON.stringify({ type: "shutdown" })}\n`);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const graceful = await Promise.race([
      this.exited!.then(() => true),
      new Promise<false>((resolve) => {
        timer = setTimeout(() => resolve(false), this.backendOptions.shutdownTimeoutMs ?? 15_000);
      }),
    ]);
    clearTimeout(timer);
    if (graceful) return;
    if (child.pid) {
      if (process.platform === "win32") {
        await new Promise<void>((resolve) => {
          execFile("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true }, () => resolve());
        });
      } else {
        try { process.kill(-child.pid, "SIGKILL"); } catch { /* 进程可能已经退出。 */ }
      }
    }
    throw new Error("后端未在 15 秒内完成清理，已请求强制结束；请核验正在修改的文件");
  }
}
