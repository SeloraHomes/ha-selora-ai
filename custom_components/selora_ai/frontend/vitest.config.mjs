import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Panel/shared code, plus the build tooling at the frontend root.
    include: ["src/**/__tests__/**/*.test.js", "__tests__/**/*.test.js"],
  },
});
