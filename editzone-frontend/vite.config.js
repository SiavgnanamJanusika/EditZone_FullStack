import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const GOOGLE_GSI_SCRIPT = "https://accounts.google.com/gsi/client";

function googleIdentityEnglishLocale() {
  return {
    name: "google-identity-english-locale",
    enforce: "pre",
    transform(code, id) {
      if (!id.includes("@react-oauth/google") || !code.includes(GOOGLE_GSI_SCRIPT)) return null;
      return code.replaceAll(GOOGLE_GSI_SCRIPT, `${GOOGLE_GSI_SCRIPT}?hl=en`);
    },
  };
}

export default defineConfig({
  plugins: [googleIdentityEnglishLocale(), react()],
  optimizeDeps: {
    exclude: ["@react-oauth/google"],
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/socket.io": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
    },
  },
});
