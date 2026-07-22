// packages/hermes-engineering/src/main.ts
import { realpathSync as realpathSync3 } from "node:fs";
import { fileURLToPath } from "node:url";

// packages/hermes-engineering/src/handlers/capture-target.ts
import { execFileSync as execFileSync2 } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  chmodSync,
  closeSync,
  existsSync,
  fsyncSync,
  lstatSync as lstatSync2,
  openSync,
  readFileSync,
  realpathSync as realpathSync2,
  renameSync,
  unlinkSync,
  writeFileSync
} from "node:fs";
import {
  basename,
  dirname,
  isAbsolute as isAbsolute2,
  join as join2,
  relative as relative2,
  resolve as resolve3,
  sep as sep2
} from "node:path";

// packages/hermes-engineering/src/shims/qwenCore.ts
function unquoteCStylePath(s) {
  if (!s.startsWith('"') || !s.endsWith('"') || s.length < 2) return s;
  const inner = s.slice(1, -1);
  const bytes = [];
  let i = 0;
  while (i < inner.length) {
    const c = inner.charCodeAt(i);
    if (c !== 92) {
      const cp = inner.codePointAt(i);
      if (cp === void 0) {
        i++;
        continue;
      }
      const ch = String.fromCodePoint(cp);
      bytes.push(...Buffer.from(ch, "utf8"));
      i += ch.length;
      continue;
    }
    const next = inner[i + 1];
    if (next === void 0) {
      bytes.push(92);
      i++;
      continue;
    }
    switch (next) {
      case "a":
        bytes.push(7);
        i += 2;
        break;
      case "b":
        bytes.push(8);
        i += 2;
        break;
      case "f":
        bytes.push(12);
        i += 2;
        break;
      case "v":
        bytes.push(11);
        i += 2;
        break;
      case "t":
        bytes.push(9);
        i += 2;
        break;
      case "n":
        bytes.push(10);
        i += 2;
        break;
      case "r":
        bytes.push(13);
        i += 2;
        break;
      case '"':
        bytes.push(34);
        i += 2;
        break;
      case "\\":
        bytes.push(92);
        i += 2;
        break;
      default:
        if (next >= "0" && next <= "7") {
          let octal = "";
          while (octal.length < 3 && i + 1 + octal.length < inner.length && (inner[i + 1 + octal.length] ?? "") >= "0" && (inner[i + 1 + octal.length] ?? "") <= "7") {
            octal += inner[i + 1 + octal.length];
          }
          bytes.push(parseInt(octal, 8) & 255);
          i += 1 + octal.length;
        } else {
          bytes.push(...Buffer.from(next, "utf8"));
          i += 2;
        }
    }
  }
  return Buffer.from(bytes).toString("utf8");
}

// third_party/qwen-code/packages/cli/src/commands/review/lib/diff-plan.ts
var TEST_RE = /(^|\/)(__tests__|__snapshots__|__mocks__|tests?|spec|integration-tests|e2e)\/|\.(test|spec)\.[cm]?[jt]sx?$|_test\.(go|py|rb)$|(^|\/)test_[^/]+\.py$|(^|\/)src\/test\//;
var GENERATED_RE = /(^|\/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lock(b)?|Cargo\.lock|go\.sum|poetry\.lock|Gemfile\.lock|composer\.lock|NOTICES\.txt)$|\.snap$|\.min\.(js|css)$|(^|\/)(dist|build|vendor|node_modules)\//;
var DOCS_EXT = String.raw`\.(md|mdx|rst|txt|adoc)$`;
var DOCS_RE = new RegExp(
  `(^|/)(docs|doc|documentation|website)/.*${DOCS_EXT}|^[^/]+${DOCS_EXT}`
);
function classifyPath(path) {
  if (GENERATED_RE.test(path)) return "generated";
  if (TEST_RE.test(path)) return "test";
  if (DOCS_RE.test(path)) return "docs";
  return "source";
}
var DEFAULT_MAX_CHUNK_LINES = 400;
var MAX_CHUNK_CHARS = 2e4;
var HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;
function unquote(raw) {
  return unquoteCStylePath(raw.trim());
}
function stripPrefix(p) {
  return p.startsWith("a/") || p.startsWith("b/") ? p.slice(2) : p;
}
function cleanPath(raw) {
  return stripPrefix(unquote(raw));
}
function readQuoted(s, i) {
  if (s[i] !== '"') return null;
  let j = i + 1;
  while (j < s.length) {
    if (s[j] === "\\") {
      j += 2;
      continue;
    }
    if (s[j] === '"') return [s.slice(i, j + 1), j + 1];
    j++;
  }
  return null;
}
function splitHeaderPaths(rest) {
  if (rest.startsWith('"')) {
    const first = readQuoted(rest, 0);
    if (!first || rest[first[1]] !== " ") return null;
    const after = first[1] + 1;
    const second = rest[after] === '"' ? readQuoted(rest, after) : [rest.slice(after), rest.length];
    return second ? [first[0], second[0]] : null;
  }
  const q = rest.indexOf(' "');
  if (q > 0 && rest.endsWith('"')) return [rest.slice(0, q), rest.slice(q + 1)];
  const n = rest.length;
  if (n >= 5 && (n - 5) % 2 === 0) {
    const len = (n - 5) / 2;
    const old = rest.slice(0, 2 + len);
    const neu = rest.slice(3 + len);
    if (old.startsWith("a/") && neu.startsWith("b/") && rest[2 + len] === " " && old.slice(2) === neu.slice(2)) {
      return [old, neu];
    }
  }
  const idx = rest.lastIndexOf(" b/");
  if (idx > 0) return [rest.slice(0, idx), rest.slice(idx + 1)];
  return null;
}
function parseDiff(diffText) {
  const lines = diffText.split("\n");
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  const total = lines.length;
  const files = [];
  let cur = null;
  let curHunk = null;
  let oldPath = "";
  let newCursor = 0;
  const noteAdded = (f, line) => {
    const last = f.addedRanges[f.addedRanges.length - 1];
    if (last && last.end === line - 1) last.end = line;
    else f.addedRanges.push({ start: line, end: line });
  };
  const closeHunk = (endLine) => {
    if (curHunk) {
      curHunk.diffEnd = endLine;
      curHunk = null;
    }
  };
  const closeFile = (endLine) => {
    if (cur) {
      closeHunk(endLine);
      cur.diffEnd = endLine;
      cur.kind = classifyPath(cur.path);
      files.push(cur);
      cur = null;
    }
  };
  for (let i = 0; i < total; i++) {
    const line = lines[i];
    const n = i + 1;
    if (line.startsWith("diff --git ")) {
      closeFile(n - 1);
      oldPath = "";
      const tokens = splitHeaderPaths(line.slice("diff --git ".length));
      cur = {
        path: tokens ? cleanPath(tokens[1]) : "(unknown)",
        kind: "source",
        // refined once the real path is known, below
        diffStart: n,
        diffEnd: n,
        hunks: [],
        addedRanges: [],
        addedLines: 0,
        removedLines: 0,
        binary: false
      };
      continue;
    }
    if (!cur) continue;
    if (line.startsWith("Binary files ") || line.startsWith("GIT binary patch")) {
      cur.binary = true;
      continue;
    }
    if (!curHunk) {
      if (line.startsWith("rename to ")) {
        cur.path = unquote(line.slice("rename to ".length));
        continue;
      }
      if (line.startsWith("--- ")) {
        const p = line.slice(4);
        if (p !== "/dev/null") oldPath = cleanPath(p);
        continue;
      }
      if (line.startsWith("+++ ")) {
        const p = line.slice(4);
        if (p !== "/dev/null") cur.path = cleanPath(p);
        else if (oldPath) cur.path = oldPath;
        cur.kind = classifyPath(cur.path);
        continue;
      }
    }
    const hm = HUNK_RE.exec(line);
    if (hm) {
      closeHunk(n - 1);
      const newStart = Number(hm[3]);
      const newCount = hm[4] === void 0 ? 1 : Number(hm[4]);
      curHunk = {
        diffStart: n,
        diffEnd: n,
        // A pure deletion hunk is `+N,0`: it occupies no new-side lines. Keep
        // `newStart`/`newEnd` clamped so chunk labelling stays sane, and let
        // `newCount` be the authority on whether the range is real.
        newStart,
        newEnd: newCount === 0 ? newStart : newStart + newCount - 1,
        newCount
      };
      cur.hunks.push(curHunk);
      newCursor = newStart;
      continue;
    }
    if (curHunk) {
      if (line.startsWith("+")) {
        cur.addedLines++;
        noteAdded(cur, newCursor);
        newCursor++;
      } else if (line.startsWith("-")) {
        cur.removedLines++;
      } else if (line === "" || line.startsWith(" ")) {
        newCursor++;
      }
    }
  }
  closeFile(total);
  return { files, diffLines: total };
}
var MIN_SPLIT_SEGMENT = 40;
function isSafeSplitPoint(lines, n) {
  const cur = lines[n - 1];
  const prev = lines[n - 2];
  if (cur === void 0 || prev === void 0) return false;
  const isNewSide = (l) => l === "" || /^[+ ]/.test(l);
  if (!isNewSide(cur) || !isNewSide(prev)) return false;
  if (cur === "") return false;
  const content = cur.slice(1);
  if (content.length === 0 || /^\s/.test(content)) return false;
  return prev === "" || /^\s*$/.test(prev.slice(1));
}
function charPrefix(lines) {
  const p = new Array(lines.length + 1).fill(0);
  for (let i = 0; i < lines.length; i++) p[i + 1] = p[i] + lines[i].length + 1;
  return p;
}
function charsIn(prefix, s, e) {
  return prefix[e] - prefix[s - 1];
}
function splitUnit(unit, lines, prefix, maxChunkLines, bodyStart) {
  const over = (s, e) => e - s + 1 > maxChunkLines || charsIn(prefix, s, e) > MAX_CHUNK_CHARS;
  const bigEnough = (s, e) => e - s + 1 >= MIN_SPLIT_SEGMENT || charsIn(prefix, s, e) >= MAX_CHUNK_CHARS / 2;
  if (!over(unit.start, unit.end)) return [unit];
  const newLineOf = /* @__PURE__ */ new Map();
  let newLine = unit.newStart;
  for (let n = bodyStart; n <= unit.end; n++) {
    const c = lines[n - 1]?.[0];
    if (c === " " || c === "+") newLineOf.set(n, newLine++);
  }
  const out = [];
  let segStart = unit.start;
  while (over(segStart, unit.end)) {
    const upper = Math.min(unit.end, segStart + maxChunkLines - 1);
    let cut = -1;
    for (let n = upper; n > bodyStart; n--) {
      if (!isSafeSplitPoint(lines, n)) continue;
      if (over(segStart, n - 1)) continue;
      if (!bigEnough(segStart, n - 1)) continue;
      cut = n;
      break;
    }
    if (cut < 0) {
      for (let n = upper + 1; n <= unit.end; n++) {
        if (isSafeSplitPoint(lines, n) && bigEnough(segStart, n - 1)) {
          cut = n;
          break;
        }
      }
    }
    if (cut < 0) break;
    out.push({ ...unit, start: segStart, end: cut - 1 });
    segStart = cut;
  }
  out.push({ ...unit, start: segStart, end: unit.end });
  for (const seg of out) {
    let lo = Number.POSITIVE_INFINITY;
    let hi = -1;
    for (let n = seg.start; n <= seg.end; n++) {
      const nl = newLineOf.get(n);
      if (nl === void 0) continue;
      lo = Math.min(lo, nl);
      hi = Math.max(hi, nl);
    }
    seg.newStart = Number.isFinite(lo) ? lo : unit.newStart;
    seg.newEnd = hi >= 0 ? hi : seg.newStart;
  }
  return out;
}
function planChunks(files, lines, maxChunkLines = DEFAULT_MAX_CHUNK_LINES) {
  const diffLines = lines.length;
  if (diffLines === 0) return [];
  const prefix = charPrefix(lines);
  const units = [];
  for (const f of files) {
    if (f.hunks.length === 0) {
      units.push({
        start: f.diffStart,
        end: f.diffEnd,
        path: f.path,
        newStart: 0,
        newEnd: 0
      });
      continue;
    }
    f.hunks.forEach((h, idx) => {
      const unit = {
        // The first hunk swallows the file header so the chunk that owns it
        // shows the agent which file it is looking at.
        start: idx === 0 ? f.diffStart : h.diffStart,
        end: h.diffEnd,
        path: f.path,
        newStart: h.newStart,
        newEnd: h.newEnd
      };
      units.push(
        ...splitUnit(unit, lines, prefix, maxChunkLines, h.diffStart + 1)
      );
    });
  }
  const chunks = [];
  let cur = null;
  const flush = () => {
    if (cur) {
      cur.lines = cur.endLine - cur.startLine + 1;
      cur.chars = charsIn(prefix, cur.startLine, cur.endLine);
      let widest = 0;
      for (let n = cur.startLine; n <= cur.endLine; n++) {
        if (lines[n - 1].length > widest) widest = lines[n - 1].length;
      }
      cur.maxLineChars = widest;
      chunks.push(cur);
      cur = null;
    }
  };
  for (const u of units) {
    const uLines = u.end - u.start + 1;
    if (cur && (u.end - cur.startLine + 1 > maxChunkLines || charsIn(prefix, cur.startLine, u.end) > MAX_CHUNK_CHARS)) {
      flush();
    }
    if (!cur) {
      cur = {
        id: chunks.length + 1,
        startLine: u.start,
        endLine: u.end,
        lines: uLines,
        chars: 0,
        // set in flush(), once the chunk's final range is known
        maxLineChars: 0,
        // ditto
        oversized: uLines > maxChunkLines || charsIn(prefix, u.start, u.end) > MAX_CHUNK_CHARS,
        files: []
      };
    } else {
      cur.endLine = u.end;
    }
    const last = cur.files[cur.files.length - 1];
    if (last && last.path === u.path) {
      last.newStart = Math.min(last.newStart || u.newStart, u.newStart);
      last.newEnd = Math.max(last.newEnd, u.newEnd);
    } else {
      cur.files.push({ path: u.path, newStart: u.newStart, newEnd: u.newEnd });
    }
    if (cur.oversized) flush();
  }
  flush();
  return chunks;
}
function buildDiffPlan(diffText, maxChunkLines = DEFAULT_MAX_CHUNK_LINES) {
  const { files, diffLines } = parseDiff(diffText);
  const lines = diffText.split("\n");
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  const linesOf = (kind) => files.filter((f) => f.kind === kind).reduce((n, f) => n + (f.diffEnd - f.diffStart + 1), 0);
  const chunks = planChunks(files, lines, maxChunkLines);
  if (!chunksCoverDiff(chunks, diffLines)) {
    throw new Error(
      `diff-plan: chunks do not tile the diff (${chunks.length} chunks over ${diffLines} lines). Refusing to plan a review with a coverage hole.`
    );
  }
  return {
    diffLines,
    diffChars: diffText.length,
    srcDiffLines: linesOf("source"),
    testDiffLines: linesOf("test"),
    generatedDiffLines: linesOf("generated"),
    docsDiffLines: linesOf("docs"),
    files,
    chunks
  };
}
function chunksCoverDiff(chunks, diffLines) {
  if (diffLines === 0) return chunks.length === 0;
  const sorted = [...chunks].sort((a, b) => a.startLine - b.startLine);
  let expected = 1;
  for (const c of sorted) {
    if (c.startLine !== expected) return false;
    if (c.endLine < c.startLine) return false;
    expected = c.endLine + 1;
  }
  return expected === diffLines + 1;
}

// third_party/qwen-code/packages/cli/src/commands/review/lib/diff-flags.ts
var PINNED_DIFF_CONFIG = [
  "-c",
  "diff.suppressBlankEmpty=false"
];
var PINNED_DIFF_FLAGS = [
  "--no-ext-diff",
  "--no-textconv",
  "--no-color",
  "--unified=3",
  "--src-prefix=a/",
  "--dst-prefix=b/",
  "--find-renames",
  "--no-relative",
  "--ignore-submodules=none",
  "--submodule=short"
];
var NULL_DEVICE = process.platform === "win32" ? "NUL" : "/dev/null";
var LITERAL_PATHSPECS = "--literal-pathspecs";

// third_party/qwen-code/packages/cli/src/commands/review/lib/local-diff.ts
import { lstatSync, statSync, realpathSync } from "node:fs";
import { join, relative, resolve, isAbsolute, sep } from "node:path";

// third_party/qwen-code/packages/cli/src/commands/review/lib/git.ts
import { execFileSync } from "node:child_process";
var GIT_TIMEOUT_MS = 12e4;
function gitOpts() {
  return {
    timeout: GIT_TIMEOUT_MS,
    env: { ...process.env, GIT_TERMINAL_PROMPT: "0" }
  };
}
function git(...args) {
  return execFileSync("git", args, { ...gitOpts(), encoding: "utf8" }).replace(/\r\n/g, "\n").trim();
}
function gitWithInput(input, args) {
  return execFileSync("git", args, { ...gitOpts(), encoding: "utf8", input }).replace(/\r\n/g, "\n").trim();
}
function gitOpt(...args) {
  try {
    return execFileSync("git", args, {
      ...gitOpts(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"]
    }).replace(/\r\n/g, "\n").trim();
  } catch {
    return null;
  }
}
function refExists(ref) {
  return gitOpt("rev-parse", "--verify", "--quiet", ref) !== null;
}
function gitRaw(...args) {
  return execFileSync("git", args, {
    ...gitOpts(),
    maxBuffer: 512 * 1024 * 1024,
    stdio: ["ignore", "pipe", "pipe"]
  });
}
function gitRawTolerateDiff(...args) {
  try {
    return gitRaw(...args);
  } catch (err) {
    const e = err;
    if (e.status === 1 && e.stdout && e.stdout.length > 0) return e.stdout;
    throw err;
  }
}

// third_party/qwen-code/packages/cli/src/commands/review/lib/local-diff.ts
var MAX_UNTRACKED_BYTES = 1e6;
var MAX_UNTRACKED_FILES = 500;
var MAX_UNTRACKED_TOTAL_BYTES = 1e7;
function emptyTree(repoRoot) {
  return gitWithInput(Buffer.alloc(0), [
    "-C",
    repoRoot,
    "hash-object",
    "-t",
    "tree",
    "--stdin"
  ]);
}
function describeUndiffable(abs, st) {
  if (st.isDirectory()) {
    return "is a directory (an embedded git repository, most likely) \u2014 git cannot diff it as a file";
  }
  if (st.isSymbolicLink()) {
    try {
      return statSync(abs).isDirectory() ? "is a symlink to a directory \u2014 git cannot diff it as a file" : null;
    } catch {
      return null;
    }
  }
  return null;
}
function toRepoPathspec(repoRoot, file) {
  let abs = resolve(process.cwd(), file);
  try {
    abs = realpathSync(abs);
  } catch {
  }
  const rel = relative(repoRoot, abs);
  const escapes = rel === "" || rel === ".." || rel.startsWith(".." + sep) || isAbsolute(rel);
  if (escapes) {
    throw new Error(
      `--file ${file} resolves to ${abs}, which is outside the repository at ${repoRoot}.`
    );
  }
  return rel.split(sep).join("/");
}
function isBinarySection(section) {
  return /^(Binary files .* differ|GIT binary patch)$/m.test(
    section.toString("utf8")
  );
}
function listUntracked(repoRoot, pathspec) {
  const args = [
    "-C",
    repoRoot,
    LITERAL_PATHSPECS,
    "ls-files",
    "--others",
    "--exclude-standard",
    "--full-name",
    "-z"
  ];
  if (pathspec) args.push("--", pathspec);
  const out = gitRaw(...args).toString("utf8");
  return out.split("\0").filter((p) => p !== "");
}
function diffUntracked(repoRoot, path) {
  return gitRawTolerateDiff(
    "-C",
    repoRoot,
    LITERAL_PATHSPECS,
    ...PINNED_DIFF_CONFIG,
    "diff",
    "--no-index",
    ...PINNED_DIFF_FLAGS,
    "--",
    NULL_DEVICE,
    path
  );
}
function captureLocalDiff(opts) {
  const { file, includeUntracked = true } = opts;
  const repoRoot = git("rev-parse", "--show-toplevel");
  const unbornHead = !refExists("HEAD");
  const base = unbornHead ? emptyTree(repoRoot) : "HEAD";
  const pathspec = file ? toRepoPathspec(repoRoot, file) : void 0;
  const trackedArgs = [
    "-C",
    repoRoot,
    LITERAL_PATHSPECS,
    ...PINNED_DIFF_CONFIG,
    "diff",
    ...PINNED_DIFF_FLAGS,
    base
  ];
  if (pathspec) trackedArgs.push("--", pathspec);
  const trackedDiff = gitRaw(...trackedArgs);
  const untracked = [];
  const skipped = [];
  const parts = [];
  if (trackedDiff.length > MAX_UNTRACKED_TOTAL_BYTES) {
    skipped.push({
      path: "tracked changes",
      bytes: trackedDiff.length,
      reason: `the tracked diff is ${Math.round(trackedDiff.length / 1e6)} MB, over the ${Math.round(MAX_UNTRACKED_TOTAL_BYTES / 1e6)} MB capture cap \u2014 review it in smaller commits`
    });
  } else {
    parts.push(trackedDiff);
  }
  if (includeUntracked) {
    const candidates = listUntracked(repoRoot, pathspec).filter(
      (p) => !p.startsWith(".qwen/tmp/") && p !== ".qwen/tmp"
    );
    if (candidates.length > MAX_UNTRACKED_FILES) {
      skipped.push({
        path: `${candidates.length} untracked files`,
        bytes: null,
        reason: `${candidates.length} untracked files exceeds the ${MAX_UNTRACKED_FILES}-file cap, so NONE of them were reviewed. A count this size usually means .gitignore does not cover a build or dependency directory. Ignore them, or stage the ones you want reviewed, or re-run with untracked capture off.`
      });
    } else {
      let budget = MAX_UNTRACKED_TOTAL_BYTES;
      for (const path of candidates) {
        let bytes;
        try {
          const abs = join(repoRoot, path);
          const st = lstatSync(abs);
          const kind = describeUndiffable(abs, st);
          if (kind) {
            skipped.push({ path, bytes: null, reason: kind });
            continue;
          }
          bytes = st.size;
        } catch (err) {
          skipped.push({
            path,
            bytes: null,
            reason: `could not be read (${err.message})`
          });
          continue;
        }
        if (bytes > MAX_UNTRACKED_BYTES) {
          skipped.push({
            path,
            bytes,
            reason: `${Math.round(bytes / 1e3)} kB exceeds the ${Math.round(
              MAX_UNTRACKED_BYTES / 1e3
            )} kB untracked-file cap`
          });
          continue;
        }
        if (bytes > budget) {
          skipped.push({
            path,
            bytes,
            reason: `the untracked capture reached its ${Math.round(MAX_UNTRACKED_TOTAL_BYTES / 1e6)} MB total cap`
          });
          continue;
        }
        let section;
        try {
          section = diffUntracked(repoRoot, path);
        } catch (err) {
          skipped.push({
            path,
            bytes,
            reason: `git could not diff it (${err.message.trim()})`
          });
          continue;
        }
        if (section.length > MAX_UNTRACKED_BYTES) {
          skipped.push({
            path,
            bytes: section.length,
            reason: `its diff is ${Math.round(section.length / 1e3)} kB, over the ${Math.round(MAX_UNTRACKED_BYTES / 1e3)} kB untracked-file cap (the rendered diff, not the file \u2014 unified-diff framing and a file that grew after it was measured both land here)`
          });
          continue;
        }
        if (section.length > budget) {
          skipped.push({
            path,
            bytes: section.length,
            reason: `the untracked capture reached its ${Math.round(MAX_UNTRACKED_TOTAL_BYTES / 1e6)} MB total cap`
          });
          continue;
        }
        if (isBinarySection(section)) {
          skipped.push({
            path,
            bytes,
            reason: 'is a binary file \u2014 git emits only a "Binary files differ" marker, so there is nothing for a reviewer to read'
          });
          continue;
        }
        budget -= section.length;
        parts.push(section);
        untracked.push(path);
      }
    }
  }
  return { diff: Buffer.concat(parts), untracked, skipped, unbornHead };
}

// third_party/qwen-code/packages/cli/src/commands/review/lib/merge-base.ts
function resolveMergeBase(remote, baseRefName, headRef, git2) {
  const baseFetchFailed = !git2.fetch(remote, baseRefName);
  for (const candidate of [`${remote}/${baseRefName}`, baseRefName]) {
    if (!git2.refExists(candidate)) continue;
    const mb = git2.mergeBase(candidate, headRef);
    if (mb) return { sha: mb, baseFetchFailed };
  }
  return { sha: null, baseFetchFailed };
}

// packages/hermes-engineering/src/shims/stdioHelpers.ts
var writeStdoutLine = (line) => void process.stdout.write(`${line}
`);
var writeStderrLine = (line) => void process.stderr.write(`${line}
`);

// third_party/qwen-code/packages/cli/src/commands/review/lib/heavy.ts
var HEAVY_MIN_PRE_LINES = 300;
var HEAVY_REWRITE_RATIO = 0.4;
var HEAVY_CHANGED_LINES = 800;
function classifyHeavy(input) {
  const { preLines, fileLines, changedLines, binary, kind } = input;
  const exactRatio = fileLines > 0 ? changedLines / fileLines : 0;
  const heavy = !binary && kind === "source" && // A deletion clears the volume threshold trivially but has no post-image,
  // and the whole-file invariant agents are told to read exactly that.
  fileLines > 0 && preLines >= HEAVY_MIN_PRE_LINES && (exactRatio >= HEAVY_REWRITE_RATIO || changedLines >= HEAVY_CHANGED_LINES);
  return { rewriteRatio: Math.round(exactRatio * 100) / 100, heavy };
}

// third_party/qwen-code/packages/cli/src/commands/review/lib/report.ts
function buildPlanReport(plan, postImageLines) {
  const files = plan.files.map((f) => {
    const changedLines = f.addedLines + f.removedLines;
    const fileLines = f.binary || !postImageLines ? 0 : postImageLines(f.path);
    const preLines = postImageLines ? Math.max(0, fileLines - f.addedLines + f.removedLines) : 0;
    const { rewriteRatio, heavy } = classifyHeavy({
      preLines,
      fileLines,
      changedLines,
      binary: f.binary,
      kind: f.kind
    });
    return {
      path: f.path,
      kind: f.kind,
      hunks: f.hunks.filter((h) => h.newCount > 0).map((h) => ({ newStart: h.newStart, newEnd: h.newEnd })),
      ...heavy ? {
        addedRanges: f.addedRanges,
        diffRange: { startLine: f.diffStart, endLine: f.diffEnd }
      } : {},
      addedLines: f.addedLines,
      removedLines: f.removedLines,
      changedLines,
      preLines,
      fileLines,
      rewriteRatio,
      heavy,
      binary: f.binary
    };
  });
  return {
    diffLines: plan.diffLines,
    diffChars: plan.diffChars,
    srcDiffLines: plan.srcDiffLines,
    testDiffLines: plan.testDiffLines,
    docsDiffLines: plan.docsDiffLines,
    generatedDiffLines: plan.generatedDiffLines,
    chunks: plan.chunks,
    files
  };
}
function stringifyPlanReport(report) {
  const indented = JSON.stringify(report, null, 2);
  return indented.replace(
    /\{\s*"start": (\d+),\s*"end": (\d+)\s*\}/g,
    '{ "start": $1, "end": $2 }'
  ).replace(
    /\{\s*"startLine": (\d+),\s*"endLine": (\d+)\s*\}/g,
    '{ "startLine": $1, "endLine": $2 }'
  ).replace(
    /\{\s*"newStart": (\d+),\s*"newEnd": (\d+)\s*\}/g,
    '{ "newStart": $1, "newEnd": $2 }'
  ).replace(
    /\{\s*"path": ("(?:[^"\\]|\\.)*"),\s*"newStart": (\d+),\s*"newEnd": (\d+)\s*\}/g,
    '{ "path": $1, "newStart": $2, "newEnd": $3 }'
  ) + "\n";
}

// packages/hermes-engineering/src/protocol.ts
import { resolve as resolve2 } from "node:path";
var MAX_REQUEST_BYTES = 1024 * 1024;
var REQUEST_KEYS = /* @__PURE__ */ new Set([
  "protocolVersion",
  "requestId",
  "command",
  "workspace",
  "artifactRoot",
  "input"
]);
var ENGINE_COMMANDS = /* @__PURE__ */ new Set([
  "capture-target",
  "build-prompts",
  "build-test",
  "test-efficacy",
  "check-coverage",
  "resolve-anchors",
  "compose-review",
  "cleanup"
]);
var isRecord = (value) => typeof value === "object" && value !== null && !Array.isArray(value);
var requiredString = (value, key) => {
  const field = value[key];
  if (typeof field !== "string" || field.length === 0) {
    throw new TypeError(`${key} must be a non-empty string`);
  }
  return field;
};
var rejectUnknownFields = (value, allowed) => {
  const allowedFields = new Set(allowed);
  const unknown = Object.keys(value).find((key) => !allowedFields.has(key));
  if (unknown) throw new TypeError(`unknown capture input field: ${unknown}`);
};
function parseCaptureInput(value) {
  if (!isRecord(value)) throw new TypeError("capture input must be an object");
  const kind = value.kind;
  if (kind === "local") {
    rejectUnknownFields(value, ["kind"]);
    return { kind };
  }
  if (kind === "file") {
    rejectUnknownFields(value, ["kind", "path", "base"]);
    const path = requiredString(value, "path");
    const base = value.base;
    if (base === void 0) return { kind, path };
    if (typeof base !== "string" || base.length === 0) {
      throw new TypeError("base must be a non-empty string");
    }
    return { kind, path, base };
  }
  if (kind === "range") {
    rejectUnknownFields(value, ["kind", "range"]);
    return { kind, range: requiredString(value, "range") };
  }
  if (kind === "pr") {
    rejectUnknownFields(value, ["kind", "number", "ownerRepo"]);
    if (!Number.isSafeInteger(value.number) || value.number < 1) {
      throw new TypeError("number must be a positive integer");
    }
    const ownerRepo = requiredString(value, "ownerRepo");
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(ownerRepo)) {
      throw new TypeError('ownerRepo must look like "owner/repo"');
    }
    return { kind, number: value.number, ownerRepo };
  }
  throw new TypeError("capture input kind must be local, file, range, or pr");
}
function parseRequest(value) {
  let encoded;
  try {
    encoded = JSON.stringify(value);
  } catch {
    throw new TypeError("request must be JSON-serializable");
  }
  if (encoded === void 0 || Buffer.byteLength(encoded, "utf8") > MAX_REQUEST_BYTES) {
    throw new TypeError("request must not exceed 1 MiB");
  }
  if (!isRecord(value)) {
    throw new TypeError("request must be an object");
  }
  if (value.protocolVersion !== 1) {
    throw new TypeError("request requires protocolVersion 1");
  }
  const unknownKeys = Object.keys(value).filter(
    (key) => !REQUEST_KEYS.has(key)
  );
  if (unknownKeys.length > 0) {
    throw new TypeError(`unknown request field: ${unknownKeys[0]}`);
  }
  const command = value.command;
  if (typeof command !== "string" || !ENGINE_COMMANDS.has(command)) {
    throw new TypeError("command is not supported by protocolVersion 1");
  }
  if (!isRecord(value.input)) {
    throw new TypeError("input must be an object");
  }
  return {
    protocolVersion: 1,
    requestId: requiredString(value, "requestId"),
    command,
    workspace: resolve2(requiredString(value, "workspace")),
    artifactRoot: resolve2(requiredString(value, "artifactRoot")),
    input: value.input
  };
}

// packages/hermes-engineering/src/handlers/capture-target.ts
var GIT_TIMEOUT_MS2 = 12e4;
var DIFF_NAME = "target.diff";
var PLAN_NAME = "plan.json";
var WORKTREE_PREFIX = ".hermes-review-";
var CaptureTargetError = class extends Error {
  code;
  output;
  constructor(code, message, output) {
    super(message);
    this.name = "CaptureTargetError";
    this.code = code;
    if (output !== void 0) this.output = output;
  }
};
var gitOptions = (cwd) => ({
  cwd,
  env: { ...process.env, GIT_TERMINAL_PROMPT: "0" },
  timeout: GIT_TIMEOUT_MS2,
  maxBuffer: 512 * 1024 * 1024
});
var gitText = (cwd, ...args) => execFileSync2("git", args, { ...gitOptions(cwd), encoding: "utf8" }).replace(/\r\n/g, "\n").trim();
var gitTextOptional = (cwd, ...args) => {
  try {
    return gitText(cwd, ...args);
  } catch {
    return null;
  }
};
var gitSucceeds = (cwd, ...args) => {
  try {
    gitText(cwd, ...args);
    return true;
  } catch {
    return false;
  }
};
var gitRaw2 = (cwd, ...args) => execFileSync2("git", args, gitOptions(cwd));
var escaped = (root, candidate) => {
  const rel = relative2(root, candidate);
  return rel === ".." || rel.startsWith(`..${sep2}`) || isAbsolute2(rel);
};
var validatedDirectory = (path, label) => {
  let stat;
  try {
    stat = lstatSync2(path);
  } catch (cause) {
    throw new CaptureTargetError(
      "invalid_path",
      `${label} could not be inspected: ${cause.message}`
    );
  }
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    throw new CaptureTargetError(
      "invalid_path",
      `${label} must be a real directory, not a symlink`
    );
  }
  return realpathSync2(path);
};
var validatedArtifactRoot = (path) => {
  const root = validatedDirectory(path, "artifactRoot");
  if (process.platform !== "win32" && (lstatSync2(root).mode & 63) !== 0) {
    throw new CaptureTargetError(
      "invalid_artifact_root",
      "artifactRoot must be private to the current user"
    );
  }
  return root;
};
var repositoryFor = (workspace) => {
  const canonicalWorkspace = validatedDirectory(workspace, "workspace");
  const rawRoot = gitTextOptional(
    canonicalWorkspace,
    "rev-parse",
    "--show-toplevel"
  );
  if (!rawRoot) {
    throw new CaptureTargetError(
      "invalid_repository",
      "workspace is not a Git repository"
    );
  }
  const repoRoot = realpathSync2(rawRoot);
  if (escaped(repoRoot, canonicalWorkspace)) {
    throw new CaptureTargetError(
      "invalid_repository",
      "workspace is outside the resolved Git repository"
    );
  }
  return { repoRoot, workspace: canonicalWorkspace };
};
var validateRelativeFile = (workspace, repoRoot, path) => {
  if (path.length === 0 || isAbsolute2(path) || path.includes("\0")) {
    throw new CaptureTargetError(
      "invalid_target",
      "file path must be a non-empty repository-relative path"
    );
  }
  const absolute = resolve3(workspace, path);
  if (escaped(repoRoot, absolute) || absolute === repoRoot) {
    throw new CaptureTargetError(
      "invalid_target",
      "file path escapes the repository"
    );
  }
  let existing = absolute;
  while (!existsSync(existing) && existing !== repoRoot)
    existing = dirname(existing);
  let canonicalExisting;
  try {
    canonicalExisting = realpathSync2(existing);
  } catch (cause) {
    throw new CaptureTargetError(
      "invalid_target",
      `file path could not be resolved: ${cause.message}`
    );
  }
  if (escaped(repoRoot, canonicalExisting)) {
    throw new CaptureTargetError(
      "invalid_target",
      "file path resolves outside the repository"
    );
  }
  return relative2(repoRoot, absolute).split(sep2).join("/");
};
var resolveCommit = (repoRoot, ref) => {
  if (ref.length === 0 || ref.startsWith("-") || /[\0\r\n]/.test(ref)) {
    throw new CaptureTargetError("invalid_target", `invalid Git ref: ${ref}`);
  }
  const sha = gitTextOptional(
    repoRoot,
    "rev-parse",
    "--verify",
    `${ref}^{commit}`
  );
  if (!sha) {
    throw new CaptureTargetError(
      "invalid_target",
      `Git ref does not resolve: ${ref}`
    );
  }
  return sha;
};
var emptyTree2 = (repoRoot) => execFileSync2("git", ["hash-object", "-t", "tree", "--stdin"], {
  ...gitOptions(repoRoot),
  input: Buffer.alloc(0),
  encoding: "utf8"
}).trim();
var withWorkingDirectory = (cwd, action) => {
  const previous = process.cwd();
  try {
    process.chdir(cwd);
    return action();
  } finally {
    process.chdir(previous);
  }
};
var captureLocal = (input, repoRoot, workspace) => {
  const requestedPath = input.kind === "file" ? input.path : void 0;
  const path = requestedPath === void 0 ? void 0 : validateRelativeFile(workspace, repoRoot, requestedPath);
  const local = withWorkingDirectory(
    workspace,
    () => captureLocalDiff(
      requestedPath === void 0 ? {} : { file: requestedPath }
    )
  );
  const currentHead = gitTextOptional(
    repoRoot,
    "rev-parse",
    "--verify",
    "HEAD"
  );
  let baseRef = currentHead ?? emptyTree2(repoRoot);
  let diff = local.diff;
  if (input.kind === "file" && input.base !== void 0) {
    baseRef = resolveCommit(repoRoot, input.base);
    if (!local.untracked.includes(path)) {
      diff = gitRaw2(
        repoRoot,
        LITERAL_PATHSPECS,
        ...PINNED_DIFF_CONFIG,
        "diff",
        ...PINNED_DIFF_FLAGS,
        baseRef,
        "--",
        path
      );
    }
  }
  return {
    targetKind: input.kind,
    baseRef,
    headRef: "WORKTREE",
    diff,
    worktreePath: null,
    postImageRoot: repoRoot,
    skipped: local.skipped
  };
};
var rangeIdentity = (repoRoot, range) => {
  const triple = range.indexOf("...");
  if (triple >= 0) {
    if (range.indexOf("...", triple + 3) >= 0) {
      throw new CaptureTargetError(
        "invalid_target",
        `invalid Git range: ${range}`
      );
    }
    const left = range.slice(0, triple);
    const right = range.slice(triple + 3);
    const leftSha = resolveCommit(repoRoot, left);
    const headRef2 = resolveCommit(repoRoot, right);
    const baseRef = gitTextOptional(repoRoot, "merge-base", leftSha, headRef2);
    if (!baseRef) {
      throw new CaptureTargetError("invalid_target", "range has no merge base");
    }
    return { baseRef, headRef: headRef2 };
  }
  const double = range.indexOf("..");
  if (double >= 0) {
    if (range.indexOf("..", double + 2) >= 0) {
      throw new CaptureTargetError(
        "invalid_target",
        `invalid Git range: ${range}`
      );
    }
    return {
      baseRef: resolveCommit(repoRoot, range.slice(0, double)),
      headRef: resolveCommit(repoRoot, range.slice(double + 2))
    };
  }
  const headRef = resolveCommit(repoRoot, range);
  return {
    baseRef: gitTextOptional(repoRoot, "rev-parse", "--verify", `${headRef}^`) ?? emptyTree2(repoRoot),
    headRef
  };
};
var worktreePathFor = (repoRoot, runId) => {
  const suffix = createHash("sha256").update(`${repoRoot}\0${runId}`).digest("hex").slice(0, 16);
  return join2(dirname(repoRoot), `${WORKTREE_PREFIX}${suffix}`);
};
var addWorktree = (repoRoot, worktreePath, headRef) => {
  if (existsSync(worktreePath)) {
    throw new CaptureTargetError(
      "worktree_exists",
      `disposable worktree path already exists: ${worktreePath}`
    );
  }
  gitText(
    repoRoot,
    "worktree",
    "add",
    "--quiet",
    "--detach",
    worktreePath,
    headRef
  );
};
var captureRange = (input, repoRoot, runId) => {
  const { baseRef, headRef } = rangeIdentity(repoRoot, input.range);
  const worktreePath = worktreePathFor(repoRoot, runId);
  addWorktree(repoRoot, worktreePath, headRef);
  try {
    return {
      targetKind: "range",
      baseRef,
      headRef,
      diff: gitRaw2(
        repoRoot,
        ...PINNED_DIFF_CONFIG,
        "diff",
        ...PINNED_DIFF_FLAGS,
        `${baseRef}..${headRef}`
      ),
      worktreePath,
      postImageRoot: worktreePath,
      skipped: []
    };
  } catch (cause) {
    removeWorktree(worktreePath);
    throw cause;
  }
};
var pullRequestContext = (repoRoot, input) => {
  let parsed;
  try {
    const raw = execFileSync2(
      "gh",
      [
        "pr",
        "view",
        String(input.number),
        "--repo",
        input.ownerRepo,
        "--json",
        "url,headRefName,headRefOid,baseRefName,baseRefOid"
      ],
      {
        cwd: repoRoot,
        env: process.env,
        encoding: "utf8",
        timeout: GIT_TIMEOUT_MS2,
        maxBuffer: 16 * 1024 * 1024
      }
    );
    parsed = JSON.parse(raw);
  } catch (cause) {
    throw new CaptureTargetError(
      "pr_context_unresolved",
      `could not resolve ${input.ownerRepo}#${input.number} with gh: ${cause.message}`
    );
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new CaptureTargetError(
      "pr_context_unresolved",
      "gh PR context must be an object"
    );
  }
  const value = parsed;
  const field = (name) => {
    const candidate = value[name];
    if (typeof candidate !== "string" || candidate.length === 0) {
      throw new CaptureTargetError(
        "pr_context_unresolved",
        `gh PR context field ${name} must be a non-empty string`
      );
    }
    return candidate;
  };
  const context = {
    url: field("url"),
    headRefName: field("headRefName"),
    headRefOid: field("headRefOid").toLowerCase(),
    baseRefName: field("baseRefName"),
    baseRefOid: field("baseRefOid").toLowerCase()
  };
  if (!/^[0-9a-f]{40,64}$/.test(context.headRefOid) || !/^[0-9a-f]{40,64}$/.test(context.baseRefOid) || !gitSucceeds(
    repoRoot,
    "check-ref-format",
    `refs/heads/${context.headRefName}`
  ) || !gitSucceeds(
    repoRoot,
    "check-ref-format",
    `refs/heads/${context.baseRefName}`
  )) {
    throw new CaptureTargetError(
      "pr_context_unresolved",
      "gh PR context contains an invalid ref name or object ID"
    );
  }
  let actualUrl;
  try {
    actualUrl = new URL(context.url);
  } catch {
    throw new CaptureTargetError(
      "pr_repository_mismatch",
      "gh PR context returned an invalid repository URL"
    );
  }
  const expectedPath = `/${input.ownerRepo}/pull/${input.number}`.toLowerCase();
  if (actualUrl.protocol !== "https:" || actualUrl.hostname.toLowerCase() !== "github.com" || actualUrl.pathname.replace(/\/$/u, "").toLowerCase() !== expectedPath) {
    throw new CaptureTargetError(
      "pr_repository_mismatch",
      `gh resolved ${context.url}, not ${input.ownerRepo}#${input.number}`
    );
  }
  return context;
};
var fetchVerifiedRef = (repoRoot, repositoryUrl, ref, expectedSha, mismatchCode) => {
  try {
    gitText(repoRoot, "fetch", "--quiet", "--no-tags", repositoryUrl, ref);
  } catch (cause) {
    throw new CaptureTargetError(
      "pr_fetch_failed",
      `could not fetch ${ref} from the validated PR repository: ${cause.message}`
    );
  }
  const fetchedSha = resolveCommit(repoRoot, "FETCH_HEAD");
  if (fetchedSha !== expectedSha) {
    throw new CaptureTargetError(
      mismatchCode,
      `fetched ${ref} at ${fetchedSha}, but gh resolved ${expectedSha}`
    );
  }
  return fetchedSha;
};
var capturePullRequest = (input, repoRoot, runId) => {
  const context = pullRequestContext(repoRoot, input);
  const repositoryUrl = `https://github.com/${input.ownerRepo}.git`;
  const headRef = fetchVerifiedRef(
    repoRoot,
    repositoryUrl,
    `refs/pull/${input.number}/head`,
    context.headRefOid,
    "pr_head_changed"
  );
  const baseSha = fetchVerifiedRef(
    repoRoot,
    repositoryUrl,
    `refs/heads/${context.baseRefName}`,
    context.baseRefOid,
    "pr_base_changed"
  );
  const remoteIdentity = input.ownerRepo;
  const probe = {
    fetch: (remoteName, ref) => remoteName === remoteIdentity && ref === context.baseRefName,
    refExists: (ref) => ref === `${remoteIdentity}/${context.baseRefName}`,
    mergeBase: (_left, right) => right === headRef ? gitTextOptional(repoRoot, "merge-base", baseSha, headRef) : null
  };
  const mergeBase = resolveMergeBase(
    remoteIdentity,
    context.baseRefName,
    headRef,
    probe
  );
  if (!mergeBase.sha) {
    throw new CaptureTargetError(
      "pr_base_unresolved",
      `could not resolve the merge base of ${context.baseRefName} and ${headRef}`
    );
  }
  const worktreePath = worktreePathFor(repoRoot, runId);
  addWorktree(repoRoot, worktreePath, headRef);
  try {
    return {
      targetKind: "pr",
      baseRef: mergeBase.sha,
      headRef,
      diff: gitRaw2(
        repoRoot,
        ...PINNED_DIFF_CONFIG,
        "diff",
        ...PINNED_DIFF_FLAGS,
        `${mergeBase.sha}..${headRef}`
      ),
      worktreePath,
      postImageRoot: worktreePath,
      skipped: [],
      prContext: {
        ownerRepo: input.ownerRepo,
        number: input.number,
        url: context.url,
        headRefName: context.headRefName,
        headSha: context.headRefOid,
        baseRefName: context.baseRefName,
        baseSha: context.baseRefOid
      }
    };
  } catch (cause) {
    removeWorktree(worktreePath);
    throw cause;
  }
};
var lineCount = (root, path) => {
  const absolute = resolve3(root, path);
  if (escaped(root, absolute)) return 0;
  try {
    const stat = lstatSync2(absolute);
    if (!stat.isFile()) return 0;
    const contents = readFileSync(absolute);
    if (contents.length === 0) return 0;
    let lines = 0;
    for (const byte of contents) if (byte === 10) lines++;
    return contents[contents.length - 1] === 10 ? lines : lines + 1;
  } catch {
    return 0;
  }
};
var atomicWrite = (root, name, contents) => {
  const destination = join2(root, name);
  if (dirname(destination) !== root) {
    throw new CaptureTargetError(
      "invalid_artifact",
      "artifact path escapes run root"
    );
  }
  const temporary = join2(
    root,
    `.${name}.${randomBytes(12).toString("hex")}.tmp`
  );
  let descriptor;
  try {
    descriptor = openSync(temporary, "wx", 384);
    writeFileSync(descriptor, contents);
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = void 0;
    renameSync(temporary, destination);
    chmodSync(destination, 384);
    return destination;
  } finally {
    if (descriptor !== void 0) closeSync(descriptor);
    try {
      unlinkSync(temporary);
    } catch {
    }
  }
};
var binarySkips = (files) => files.filter((file) => file.binary).map((file) => ({
  path: file.path,
  bytes: null,
  reason: "binary content is not represented in the unified diff"
}));
var mergeSkips = (left, right) => {
  const result = [...left];
  for (const skipped of right) {
    if (!result.some((existing) => existing.path === skipped.path))
      result.push(skipped);
  }
  return result;
};
async function captureTarget(request) {
  const input = parseCaptureInput(request.input);
  const artifactRoot = validatedArtifactRoot(request.artifactRoot);
  const runId = basename(artifactRoot);
  const { repoRoot, workspace } = repositoryFor(request.workspace);
  let captured;
  try {
    if (input.kind === "local" || input.kind === "file") {
      captured = captureLocal(input, repoRoot, workspace);
    } else if (input.kind === "range") {
      captured = captureRange(input, repoRoot, runId);
    } else {
      captured = capturePullRequest(input, repoRoot, runId);
    }
    const diffPath = atomicWrite(artifactRoot, DIFF_NAME, captured.diff);
    const plan = buildDiffPlan(captured.diff.toString("utf8"));
    const reportBase = buildPlanReport(
      plan,
      (path) => lineCount(captured.postImageRoot, path)
    );
    const skippedFiles = mergeSkips(
      captured.skipped,
      binarySkips(reportBase.files)
    );
    const report = {
      ...reportBase,
      hermes: {
        schemaVersion: 1,
        runId,
        targetKind: captured.targetKind,
        requestedTarget: input,
        baseRef: captured.baseRef,
        headRef: captured.headRef,
        ...captured.prContext ? {
          prContext: captured.prContext,
          resolvedIdentity: {
            baseRef: captured.baseRef,
            headRef: captured.headRef
          }
        } : {},
        diffSha256: createHash("sha256").update(captured.diff).digest("hex"),
        skippedFiles
      },
      diffPathAbsolute: diffPath
    };
    const planPath = atomicWrite(
      artifactRoot,
      PLAN_NAME,
      stringifyPlanReport(report)
    );
    const output = {
      targetKind: captured.targetKind,
      baseRef: captured.baseRef,
      headRef: captured.headRef,
      diffPath,
      planPath,
      worktreePath: captured.worktreePath,
      skippedFiles,
      files: reportBase.files,
      chunks: reportBase.chunks
    };
    if (captured.diff.length === 0 && skippedFiles.length === 0) {
      throw new CaptureTargetError(
        "no_changes",
        "the selected target has no changes to review",
        output
      );
    }
    return output;
  } catch (cause) {
    if (captured?.worktreePath) {
      try {
        removeWorktree(captured.worktreePath);
      } catch {
      }
    }
    throw cause;
  }
}
var removeWorktree = (worktreePath) => {
  if (!basename(worktreePath).startsWith(WORKTREE_PREFIX)) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "refusing to remove an unknown worktree"
    );
  }
  if (!existsSync(worktreePath)) return;
  const stat = lstatSync2(worktreePath);
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "worktree path is not a real directory"
    );
  }
  const canonical = realpathSync2(worktreePath);
  if (canonical !== resolve3(worktreePath)) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "worktree path is not canonical"
    );
  }
  const gitEntry = lstatSync2(join2(canonical, ".git"));
  if (!gitEntry.isFile() || gitEntry.isSymbolicLink()) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "refusing to remove a source checkout as a disposable worktree"
    );
  }
  const common = gitText(canonical, "rev-parse", "--git-common-dir");
  const commonDirectory = realpathSync2(resolve3(canonical, common));
  execFileSync2(
    "git",
    [
      `--git-dir=${commonDirectory}`,
      "worktree",
      "remove",
      "--force",
      canonical
    ],
    gitOptions(dirname(canonical))
  );
  gitTextOptional(
    dirname(canonical),
    `--git-dir=${commonDirectory}`,
    "worktree",
    "prune"
  );
};

// packages/hermes-engineering/src/handlers/index.ts
async function dispatch(request) {
  if (request.command === "capture-target") {
    try {
      const output = await captureTarget(request);
      const incomplete = output.skippedFiles.length > 0;
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status: incomplete ? "inconclusive" : "passed",
        output: { ...output },
        diagnostics: incomplete ? [
          {
            code: "capture_incomplete",
            message: `${output.skippedFiles.length} file(s) could not be fully captured`
          }
        ] : []
      };
    } catch (cause) {
      if (cause instanceof CaptureTargetError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "failed",
          output: cause.output ? { ...cause.output } : {},
          diagnostics: [{ code: cause.code, message: cause.message }]
        };
      }
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "failed",
          output: {},
          diagnostics: [{ code: "invalid_target", message: cause.message }]
        };
      }
      throw cause;
    }
  }
  return {
    protocolVersion: 1,
    requestId: request.requestId,
    status: "inconclusive",
    output: {},
    diagnostics: [
      {
        code: "handler_not_implemented",
        message: `No handler is installed for ${request.command}`
      }
    ]
  };
}

// packages/hermes-engineering/src/main.ts
var InvalidInputError = class extends Error {
};
var requestIdFrom = (value) => {
  if (typeof value === "object" && value !== null && !Array.isArray(value) && typeof value.requestId === "string") {
    const requestId = value.requestId;
    if (requestId.length > 0) return requestId;
  }
  return "invalid";
};
var diagnosticResponse = (requestId, status, code, message) => ({
  protocolVersion: 1,
  requestId,
  status,
  output: {},
  diagnostics: [{ code, message }]
});
var asError = (value) => value instanceof Error ? value : new Error(String(value));
var internalErrorResult = (requestId, cause) => {
  const error = asError(cause);
  return {
    response: diagnosticResponse(
      requestId,
      "inconclusive",
      "internal_error",
      error.message
    ),
    exitCode: 3,
    error
  };
};
var serializeResponse = (response) => {
  const serialized = JSON.stringify(response);
  if (serialized === void 0) {
    throw new TypeError("dispatcher response is not JSON-serializable");
  }
  return serialized;
};
async function processRequest(raw, dispatchRequest = dispatch) {
  let value;
  try {
    if (Buffer.byteLength(raw, "utf8") > MAX_REQUEST_BYTES) {
      throw new TypeError("request must not exceed 1 MiB");
    }
    value = JSON.parse(raw);
    const request = parseRequest(value);
    try {
      const response = await dispatchRequest(request);
      serializeResponse(response);
      return { response, exitCode: 0 };
    } catch (cause) {
      return internalErrorResult(request.requestId, cause);
    }
  } catch (cause) {
    const error = asError(cause);
    return {
      response: diagnosticResponse(
        requestIdFrom(value),
        "failed",
        "invalid_request",
        error.message
      ),
      exitCode: 2
    };
  }
}
var readStdin = async (input) => {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of input) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += buffer.length;
    if (bytes <= MAX_REQUEST_BYTES) chunks.push(buffer);
  }
  if (bytes > MAX_REQUEST_BYTES) {
    throw new InvalidInputError("request must not exceed 1 MiB");
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(
      Buffer.concat(chunks)
    );
  } catch (cause) {
    throw new InvalidInputError("stdin must contain valid UTF-8", { cause });
  }
};
async function main(options = {}) {
  let result;
  try {
    const raw = await readStdin(options.input ?? process.stdin);
    result = await processRequest(raw, options.dispatchRequest ?? dispatch);
  } catch (cause) {
    result = cause instanceof InvalidInputError ? {
      response: diagnosticResponse(
        "invalid",
        "failed",
        "invalid_request",
        cause.message
      ),
      exitCode: 2
    } : internalErrorResult("invalid", cause);
  }
  let serialized;
  try {
    serialized = serializeResponse(result.response);
  } catch (cause) {
    result = internalErrorResult(requestIdFrom(result.response), cause);
    serialized = serializeResponse(result.response);
  }
  (options.writeOutput ?? writeStdoutLine)(serialized);
  if (options.includeStackTrace === true && result.error?.stack) {
    (options.writeError ?? writeStderrLine)(result.error.stack);
  }
  process.exitCode = result.exitCode;
}
var entrypoint = process.argv[1];
if (entrypoint && realpathSync3(fileURLToPath(import.meta.url)) === realpathSync3(entrypoint)) {
  await main();
}
export {
  main,
  processRequest
};
/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 *
 * unquoteCStylePath is derived from packages/core/src/utils/gitDiff.ts in
 * Qwen Code at the commit recorded in third_party/qwen-code/UPSTREAM.json.
 */
/**
 * @license
 * Copyright 2026 Qwen Team
 * SPDX-License-Identifier: Apache-2.0
 */
/**
 * @license
 * Copyright 2025 Qwen Team
 * SPDX-License-Identifier: Apache-2.0
 */
