/**
 * @license
 * Copyright 2025 Qwen Team
 * SPDX-License-Identifier: Apache-2.0
 */

export const writeStdoutLine = (line: string): void =>
  void process.stdout.write(`${line}\n`);
export const writeStderrLine = (line: string): void =>
  void process.stderr.write(`${line}\n`);
