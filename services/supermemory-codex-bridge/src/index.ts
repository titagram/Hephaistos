import type http from "node:http";
import { pathToFileURL } from "node:url";

import { CodexAppServer, CodexUpstreamError, type CodexRunner } from "./codex-app-server.js";
import { ConfigurationError, loadConfig, type BridgeConfig } from "./config.js";
import { createBridgeServer } from "./server.js";

const SHUTDOWN_TIMEOUT_MS = 30_000;

export async function startBridge(
  config: BridgeConfig,
  codex: CodexRunner,
): Promise<http.Server> {
  await codex.start();
  const server = createBridgeServer(config, codex);
  await new Promise<void>((resolve, reject) => {
    const onError = (error: Error) => {
      server.off("listening", onListening);
      reject(error);
    };
    const onListening = () => {
      server.off("error", onError);
      resolve();
    };
    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(config.port, config.host);
  });
  return server;
}

export async function stopBridge(
  server: http.Server,
  codex: CodexRunner,
  timeoutMs = SHUTDOWN_TIMEOUT_MS,
): Promise<void> {
  server.closeIdleConnections();
  let drained = false;
  const closed = new Promise<void>((resolve) => {
    server.close(() => {
      drained = true;
      resolve();
    });
  });
  let timer: NodeJS.Timeout | undefined;
  try {
    await Promise.race([
      closed,
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (!drained) {
    server.closeAllConnections();
    await closed;
  }
  await codex.close();
}

async function main(): Promise<void> {
  let codex: CodexAppServer | undefined;
  try {
    const config = loadConfig(process.env);
    codex = new CodexAppServer(config);
    const server = await startBridge(config, codex);
    let stopping: Promise<void> | undefined;
    const stop = () => {
      if (!stopping) {
        stopping = stopBridge(server, codex!).then(() => {
          process.exitCode = 0;
        }, () => {
          process.exitCode = 1;
        });
      }
    };
    process.once("SIGTERM", stop);
    process.once("SIGINT", stop);
  } catch (error) {
    if (codex) await codex.close().catch(() => {});
    console.error(startupErrorKind(error));
    process.exitCode = 1;
  }
}

function startupErrorKind(error: unknown): string {
  if (error instanceof CodexUpstreamError) return error.kind;
  if (error instanceof ConfigurationError) return "configuration";
  return "unavailable";
}

const entrypoint = process.argv[1];
if (entrypoint && import.meta.url === pathToFileURL(entrypoint).href) {
  void main();
}
