import { resolve } from "node:path";

import { build } from "esbuild";

const repositoryRoot = resolve(import.meta.dirname, "..");
const shim = (name) =>
  resolve(repositoryRoot, "packages/hermes-engineering/src/shims", name);

await build({
  absWorkingDir: repositoryRoot,
  entryPoints: ["packages/hermes-engineering/src/main.ts"],
  outfile: "hermes_cli/engineering_dist/hermes-engineering.mjs",
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  packages: "bundle",
  sourcemap: false,
  legalComments: "eof",
  plugins: [
    {
      name: "qwen-review-shims",
      setup(buildContext) {
        buildContext.onResolve(
          { filter: /(?:^|\/)utils\/stdioHelpers\.js$/ },
          () => ({ path: shim("stdioHelpers.ts") }),
        );
        buildContext.onResolve(
          { filter: /^@qwen-code\/qwen-code-core$/ },
          () => ({ path: shim("qwenCore.ts") }),
        );
      },
    },
  ],
});
