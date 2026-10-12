import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  use: {
    browserName: "chromium",
    headless: true,
    launchOptions: { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH },
  },
  workers: 1,
  reporter: "list",
});
