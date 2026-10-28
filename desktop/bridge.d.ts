/** 仅声明类型，避免沙箱预加载在运行时加载额外模块。 */
export type DesktopAction = "new-chat" | "open-project" | "settings" | "toggle-sidebar";

export type DesktopBridge = {
  selectDirectory: () => Promise<string | null>;
  appInfo: () => Promise<{ version: string; platform: string }>;
  retryBackend: () => Promise<void>;
  onAction: (callback: (action: DesktopAction) => void) => () => void;
};
