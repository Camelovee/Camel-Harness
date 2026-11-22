import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: fileURLToPath(new URL("./renderer", import.meta.url)),
  plugins: [react()],
  build: { outDir: "../dist/renderer", emptyOutDir: true, license: { fileName: "licenses.md" } },
});
