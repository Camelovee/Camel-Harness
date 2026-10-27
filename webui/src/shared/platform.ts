export type DesktopAction = "new-chat" | "open-project" | "settings" | "toggle-sidebar";

declare global {
  interface Window {
    camelDesktop?: {
      selectDirectory: () => Promise<string | null>;
      appInfo: () => Promise<{ version: string; platform: string }>;
      onAction: (callback: (action: DesktopAction) => void) => () => void;
    };
  }
}
