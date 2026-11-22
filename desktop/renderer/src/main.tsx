import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import DesktopApp from "./app/DesktopApp";
import "./app/theme.css";
import "./app/styles.css";
import "./app/desktop.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DesktopApp />
  </StrictMode>,
);
