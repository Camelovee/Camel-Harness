import { useEffect } from "react";
import type { ThemePreference } from "../../shared/model";

export function useAppearance(theme: ThemePreference = "system"): void {
  useEffect(() => {
    const preference = window.matchMedia?.("(prefers-color-scheme: dark)");
    const apply = () => {
      document.documentElement.dataset.theme = theme === "system"
        ? preference?.matches ? "dark" : "light" : theme;
    };
    apply();
    preference?.addEventListener("change", apply);
    return () => preference?.removeEventListener("change", apply);
  }, [theme]);
}
