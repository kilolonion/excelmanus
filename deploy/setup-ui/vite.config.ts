import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    target: "es2015",
    cssCodeSplit: false,
    assetsInlineLimit: 1024 * 1024,
  },
});
