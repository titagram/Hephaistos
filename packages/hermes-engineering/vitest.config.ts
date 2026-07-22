import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

const root = resolve(import.meta.dirname, "../..");

export default defineConfig({
  resolve: {
    alias: [
      {
        find: /^(?:.*\/)?utils\/stdioHelpers\.js$/,
        replacement: resolve(import.meta.dirname, "src/shims/stdioHelpers.ts"),
      },
      {
        find: "@qwen-code/qwen-code-core",
        replacement: resolve(import.meta.dirname, "src/shims/qwenCore.ts"),
      },
    ],
  },
  test: {
    include: [
      "tests/**/*.test.ts",
      resolve(root, "third_party/qwen-code/packages/cli/src/**/*.test.ts"),
    ],
    exclude: ["tests/fixtures/**"],
  },
});
