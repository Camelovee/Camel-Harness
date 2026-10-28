import { contextBridge, ipcRenderer } from "electron";
import type { DesktopAction, DesktopBridge } from "../bridge";

contextBridge.exposeInMainWorld("camelDesktop", {
  selectDirectory: () => ipcRenderer.invoke("desktop:select-directory"),
  appInfo: () => ipcRenderer.invoke("desktop:app-info"),
  retryBackend: () => ipcRenderer.invoke("desktop:retry"),
  onAction: (callback): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, action: DesktopAction) => callback(action);
    ipcRenderer.on("desktop:action", listener);
    return () => ipcRenderer.removeListener("desktop:action", listener);
  },
} satisfies DesktopBridge);
