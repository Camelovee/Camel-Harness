import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("camelDesktop", {
  selectDirectory: (): Promise<string | null> => ipcRenderer.invoke("desktop:select-directory"),
  appInfo: (): Promise<{ version: string; platform: string }> => ipcRenderer.invoke("desktop:app-info"),
  retryBackend: (): Promise<void> => ipcRenderer.invoke("desktop:retry"),
  onAction: (callback: (action: string) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, action: string) => callback(action);
    ipcRenderer.on("desktop:action", listener);
    return () => ipcRenderer.removeListener("desktop:action", listener);
  },
});
