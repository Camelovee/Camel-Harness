import type { DesktopBridge } from "../../../bridge";

export type { DesktopAction } from "../../../bridge";

declare global {
  interface Window {
    camelDesktop: DesktopBridge;
  }
}
