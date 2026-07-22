/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 *
 * unquoteCStylePath is derived from packages/core/src/utils/gitDiff.ts in
 * Qwen Code at the commit recorded in third_party/qwen-code/UPSTREAM.json.
 */

export interface DebugLogger {
  debug: (...args: unknown[]) => void;
}

const noOp = (): void => undefined;

export const createDebugLogger = (_tag?: string): DebugLogger => ({
  debug: noOp,
});

/**
 * Decode a path field from a `diff --git` header — handles both unquoted
 * (`b/foo.txt`) and C-style quoted (`"b/tab\there.txt"`) forms.
 *
 * Git wraps a path in `"..."` and applies C-style escaping (`\t`, `\n`,
 * `\r`, `\"`, `\\`, plus octal `\NNN` for non-ASCII bytes) whenever the
 * raw path contains a character that breaks the simple space-delimited
 * format. `core.quotepath=false` disables ONLY the octal escaping for
 * non-ASCII bytes; control chars and quotes are still escaped, so we
 * must decode them ourselves to preserve the real on-disk filename.
 *
 * Octal escapes are decoded as raw byte values then UTF-8-decoded en
 * masse so multi-byte sequences like `\346\226\207` (文) round-trip
 * correctly even though we never set quotepath=true ourselves.
 */
export function unquoteCStylePath(s: string): string {
  if (!s.startsWith('"') || !s.endsWith('"') || s.length < 2) return s;
  const inner = s.slice(1, -1);
  // Build raw bytes first so octal `\NNN` sequences (each one byte of a
  // potentially multi-byte UTF-8 character) reassemble correctly. We walk by
  // Unicode code points (not UTF-16 code units), so non-BMP characters such as
  // emoji that may appear inside a quoted path under `core.quotepath=false`
  // round-trip through UTF-8 instead of being split into lone surrogates.
  const bytes: number[] = [];
  let i = 0;
  while (i < inner.length) {
    const c = inner.charCodeAt(i);
    if (c !== 0x5c /* '\\' */) {
      const cp = inner.codePointAt(i);
      if (cp === undefined) {
        i++;
        continue;
      }
      const ch = String.fromCodePoint(cp);
      bytes.push(...Buffer.from(ch, "utf8"));
      i += ch.length;
      continue;
    }
    const next = inner[i + 1];
    if (next === undefined) {
      bytes.push(0x5c);
      i++;
      continue;
    }
    switch (next) {
      case "a":
        bytes.push(0x07);
        i += 2;
        break;
      case "b":
        bytes.push(0x08);
        i += 2;
        break;
      case "f":
        bytes.push(0x0c);
        i += 2;
        break;
      case "v":
        bytes.push(0x0b);
        i += 2;
        break;
      case "t":
        bytes.push(0x09);
        i += 2;
        break;
      case "n":
        bytes.push(0x0a);
        i += 2;
        break;
      case "r":
        bytes.push(0x0d);
        i += 2;
        break;
      case '"':
        bytes.push(0x22);
        i += 2;
        break;
      case "\\":
        bytes.push(0x5c);
        i += 2;
        break;
      default:
        if (next >= "0" && next <= "7") {
          let octal = "";
          while (
            octal.length < 3 &&
            i + 1 + octal.length < inner.length &&
            (inner[i + 1 + octal.length] ?? "") >= "0" &&
            (inner[i + 1 + octal.length] ?? "") <= "7"
          ) {
            octal += inner[i + 1 + octal.length];
          }
          bytes.push(parseInt(octal, 8) & 0xff);
          i += 1 + octal.length;
        } else {
          bytes.push(...Buffer.from(next, "utf8"));
          i += 2;
        }
    }
  }
  return Buffer.from(bytes).toString("utf8");
}
