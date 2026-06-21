import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tauri expects a fixed dev port and does not clear the screen so backend logs
// from the spawned FastAPI sidecar stay visible.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    // Proxy API calls to FastAPI so the browser never does a cross-origin request
    // in dev mode. All paths except Vite's own /@vite/ and /src/ are forwarded.
    proxy: {
      "/health": "http://127.0.0.1:8386",
      "/jobs": "http://127.0.0.1:8386",
      "/settings": "http://127.0.0.1:8386",
      "/voice-options": "http://127.0.0.1:8386",
      "/voice-references": "http://127.0.0.1:8386",
      "/source-videos": "http://127.0.0.1:8386",
      "/subtitle-styles": "http://127.0.0.1:8386",
      "/files": "http://127.0.0.1:8386",
      "/videos": "http://127.0.0.1:8386",
      "/storage": "http://127.0.0.1:8386",
      "/colab": "http://127.0.0.1:8386",
    },
  },
});
