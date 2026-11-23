import { addonPlugin, defineAddonManifest } from "@pulsesync/addon-sdk/vite";
import { defineConfig } from "vite";

const endpoint = "http://127.0.0.1:17442/";
const token = process.env.HYPRPET_PULSESYNC_TOKEN;
if (!/^[a-f0-9]{64}$/.test(token ?? "")) {
  throw new Error("Build through ./setup-pulsesync to configure the local bridge.");
}

export default defineConfig({
  define: { HYPRPET_TOKEN: JSON.stringify(token), HYPRPET_ENDPOINT: JSON.stringify(endpoint) },
  plugins: [
    addonPlugin({
      entry: "src/main.js",
      manifest: defineAddonManifest({
        id: "hyprpet-music",
        directoryName: "hyprpet-music",
        name: "Pet Music",
        description: "Локальное управление Яндекс Музыкой из Pet",
        version: "1.0.0",
        allowedUrls: [endpoint],
        requirements: { minHostApi: 1, capabilities: ["lifecycle-v1", "net-per-addon-v1", "resource-read-v1"] },
      }),
      ...(process.env.HYPRPET_ADDON_OUTPUT ? { outDir: process.env.HYPRPET_ADDON_OUTPUT } : {}),
    }),
  ],
});
