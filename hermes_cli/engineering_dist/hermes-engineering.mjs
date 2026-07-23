// packages/hermes-engineering/src/main.ts
import { realpathSync as realpathSync7 } from "node:fs";
import { fileURLToPath } from "node:url";

// packages/hermes-engineering/src/handlers/build-test.ts
import { existsSync as existsSync3, lstatSync as lstatSync3, readFileSync as readFileSync3, realpathSync as realpathSync2 } from "node:fs";
import { isAbsolute as isAbsolute3, relative as relative2, resolve as resolve3, sep as sep3 } from "node:path";

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

// third_party/qwen-code/packages/cli/src/commands/review/test-efficacy.ts
import {
  mkdirSync,
  writeFileSync,
  readFileSync as readFileSync2,
  rmSync,
  lstatSync,
  existsSync as existsSync2
} from "node:fs";
import { dirname, join as join3, isAbsolute, sep } from "node:path";

// packages/hermes-engineering/src/shims/stdioHelpers.ts
var writeStdoutLine = (line) => void process.stdout.write(`${line}
`);
var writeStderrLine = (line) => void process.stderr.write(`${line}
`);

// third_party/qwen-code/packages/cli/src/commands/review/lib/paths.ts
import { join, resolve } from "node:path";
var REVIEW_TMP_DIR = join(".qwen", "tmp");
var REVIEWS_DIR = join(".qwen", "reviews");
var REVIEW_CACHE_DIR = join(".qwen", "review-cache");

// third_party/qwen-code/packages/cli/src/commands/review/lib/workspaces.ts
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join as join2 } from "node:path";
function isWorkspaceMember(filePath, workspaceGlobs) {
  return workspaceDirFor(filePath, workspaceGlobs) !== null;
}
function workspaceDirFor(filePath, workspaceGlobs) {
  const norm = filePath.replace(/^\.\//, "");
  let owner = null;
  for (const glob of workspaceGlobs) {
    const negated = glob.startsWith("!");
    const g = glob.replace(/^!/, "").replace(/\/$/, "");
    let dir = null;
    if (g.endsWith("/*")) {
      const base = g.slice(0, -2);
      if (norm.startsWith(`${base}/`)) {
        const seg = norm.slice(base.length + 1).split("/")[0];
        if (seg) dir = `${base}/${seg}`;
      }
    } else if (norm === g || norm.startsWith(`${g}/`)) {
      dir = g;
    }
    if (dir === null) continue;
    owner = negated ? null : dir;
  }
  return owner;
}
function hasUnmodeledWorkspaceGlob(globs) {
  return globs.some((glob) => {
    const g = glob.replace(/^!/, "");
    if (!g.includes("*")) return false;
    return !/^[^*]+\/\*$/.test(g);
  });
}
function readWorkspaceGlobs(root) {
  try {
    const pkg = JSON.parse(
      readFileSync(join2(root, "package.json"), "utf8")
    );
    const ws = pkg.workspaces;
    const globs = Array.isArray(ws) ? ws : Array.isArray(ws?.packages) ? ws.packages : [];
    return globs.filter((g) => typeof g === "string");
  } catch {
    return [];
  }
}
function readRootPackage(root) {
  let pkg;
  try {
    pkg = JSON.parse(readFileSync(join2(root, "package.json"), "utf8"));
  } catch {
    return null;
  }
  const scripts = Object.keys(pkg.scripts ?? {});
  if (!scripts.includes("build") && !scripts.includes("test")) return null;
  return {
    dir: ".",
    name: typeof pkg.name === "string" && pkg.name ? pkg.name : "root",
    scripts,
    deps: []
  };
}
function readWorkspacePackages(root) {
  const globs = readWorkspaceGlobs(root);
  const dirs = /* @__PURE__ */ new Set();
  for (const glob of globs) {
    if (glob.startsWith("!")) continue;
    const g = glob.replace(/\/$/, "");
    if (g.endsWith("/*")) {
      const base = g.slice(0, -2);
      let entries;
      try {
        entries = readdirSync(join2(root, base), { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => e.name);
      } catch {
        continue;
      }
      for (const e of entries) dirs.add(`${base}/${e}`);
    } else {
      dirs.add(g);
    }
  }
  const pkgs = [];
  for (const dir of dirs) {
    if (workspaceDirFor(`${dir}/package.json`, globs) !== dir) continue;
    const manifest = join2(root, dir, "package.json");
    if (!existsSync(manifest)) continue;
    let pkg;
    try {
      pkg = JSON.parse(readFileSync(manifest, "utf8"));
    } catch {
      continue;
    }
    if (typeof pkg.name !== "string" || !pkg.name) continue;
    pkgs.push({
      dir,
      name: pkg.name,
      scripts: Object.keys(pkg.scripts ?? {}),
      deps: [
        ...Object.keys(pkg.dependencies ?? {}),
        ...Object.keys(pkg.devDependencies ?? {}),
        ...Object.keys(pkg.peerDependencies ?? {})
      ]
    });
  }
  return pkgs.sort((a, b) => a.dir.localeCompare(b.dir));
}
function affectedWorkspaces(changedFiles, workspaceGlobs) {
  const dirs = /* @__PURE__ */ new Set();
  for (const f of changedFiles) {
    const d = workspaceDirFor(f, workspaceGlobs);
    if (d) dirs.add(d);
  }
  return [...dirs].sort();
}
function buildSetFor(affected, packages, alsoBuild = []) {
  const byDir = new Map(packages.map((p) => [p.dir, p]));
  const byName = new Map(packages.map((p) => [p.name, p]));
  const dependsOn = /* @__PURE__ */ new Map();
  for (const p of packages) {
    dependsOn.set(
      p.dir,
      p.deps.map((d) => byName.get(d)?.dir).filter((d) => !!d && d !== p.dir)
    );
  }
  const consumers = new Set(affected.filter((a) => byDir.has(a)));
  let grew = true;
  while (grew) {
    grew = false;
    for (const p of packages) {
      if (consumers.has(p.dir)) continue;
      if ((dependsOn.get(p.dir) ?? []).some((d) => consumers.has(d))) {
        consumers.add(p.dir);
        grew = true;
      }
    }
  }
  const wanted = /* @__PURE__ */ new Set();
  const addDeps = (dir) => {
    if (wanted.has(dir)) return;
    wanted.add(dir);
    for (const d of dependsOn.get(dir) ?? []) addDeps(d);
  };
  for (const c of consumers) addDeps(c);
  for (const extra of alsoBuild) if (byDir.has(extra)) addDeps(extra);
  const order = [];
  const seen = /* @__PURE__ */ new Set();
  const visit = (dir) => {
    if (seen.has(dir)) return;
    seen.add(dir);
    for (const d of dependsOn.get(dir) ?? []) {
      if (wanted.has(d)) visit(d);
    }
    order.push(dir);
  };
  for (const dir of alsoBuild.filter((d) => wanted.has(d)).sort()) visit(dir);
  for (const dir of [...wanted].sort()) visit(dir);
  return order;
}

// third_party/qwen-code/packages/cli/src/commands/review/test-efficacy.ts
var FIXTURE_DIR_RE = /(^|\/)(__fixtures__|__mocks__|__snapshots__|fixtures)\//;
function planTestEfficacy(files, workspaceGlobs) {
  const tests = files.filter((f) => f.kind === "test").map((f) => f.path);
  const revert = files.filter((f) => f.kind === "source" && !FIXTURE_DIR_RE.test(f.path)).map((f) => f.path);
  const unreachable = tests.filter(
    (t) => !isWorkspaceMember(t, workspaceGlobs)
  );
  const reachable = tests.filter((t) => isWorkspaceMember(t, workspaceGlobs));
  return {
    unreachable,
    probes: revert.length > 0 ? reachable : [],
    revert
  };
}
function classifyProbeRun(exitCode, stdout, probes, stderr = "") {
  let parsed;
  const start = stdout.indexOf("{");
  if (start >= 0) {
    try {
      parsed = JSON.parse(stdout.slice(start));
    } catch {
      parsed = void 0;
    }
  }
  if (!parsed) {
    const why = stderr.trim().split("\n").slice(-3).join(" ").slice(0, 300);
    return probes.map((file) => ({
      file,
      verdict: "inconclusive",
      detail: `runner produced no parseable JSON (exit ${exitCode})${why ? `: ${why}` : ""}`
    }));
  }
  const byFile = parsed.testResults ?? [];
  return probes.map((file) => {
    const result = byFile.find(
      (r) => (r.name ?? "").endsWith(`/${file}`) || r.name === file
    );
    const assertions = result?.assertionResults ?? [];
    const failed = assertions.filter((a) => a.status === "failed").length;
    const passed = assertions.filter((a) => a.status === "passed").length;
    if (!result || assertions.length === 0) {
      return {
        file,
        verdict: "inconclusive",
        detail: `collected no tests with the source reverted (run exit ${exitCode}) \u2014 likely a compile or import error, which is not evidence either way`
      };
    }
    if (failed > 0) {
      return {
        file,
        verdict: "gated",
        detail: `${failed} assertion(s) failed with the source reverted \u2014 this test catches the change`
      };
    }
    if (passed === 0) {
      return {
        file,
        verdict: "inconclusive",
        detail: `${assertions.length} test(s) collected but none executed with the source reverted (all skipped) \u2014 not evidence either way`
      };
    }
    return {
      file,
      verdict: "inert",
      detail: `all ${passed} test(s) still PASSED with the source change reverted \u2014 this test does not gate the change`
    };
  });
}
function safeRmWithin(worktree, relPath) {
  const parts = relPath.split(/[/\\]+/).filter((s) => s && s !== ".");
  let cur = worktree;
  for (let i = 0; i < parts.length; i++) {
    cur = join3(cur, parts[i]);
    let st;
    try {
      st = lstatSync(cur);
    } catch {
      return;
    }
    if (st.isSymbolicLink() && i < parts.length - 1) {
      throw new Error(
        `refusing to delete through a symlink: ${relPath} (ancestor ${parts.slice(0, i + 1).join("/")} is a symlink)`
      );
    }
  }
  rmSync(cur, { force: true });
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
import { lstatSync as lstatSync2, statSync, realpathSync } from "node:fs";
import { join as join4, relative, resolve as resolve2, isAbsolute as isAbsolute2, sep as sep2 } from "node:path";

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
  let abs = resolve2(process.cwd(), file);
  try {
    abs = realpathSync(abs);
  } catch {
  }
  const rel = relative(repoRoot, abs);
  const escapes = rel === "" || rel === ".." || rel.startsWith(".." + sep2) || isAbsolute2(rel);
  if (escapes) {
    throw new Error(
      `--file ${file} resolves to ${abs}, which is outside the repository at ${repoRoot}.`
    );
  }
  return rel.split(sep2).join("/");
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
          const abs = join4(repoRoot, path);
          const st = lstatSync2(abs);
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
function resolveMergeBase(remote, baseRefName, headRef, git3) {
  const baseFetchFailed = !git3.fetch(remote, baseRefName);
  for (const candidate of [`${remote}/${baseRefName}`, baseRefName]) {
    if (!git3.refExists(candidate)) continue;
    const mb = git3.mergeBase(candidate, headRef);
    if (mb) return { sha: mb, baseFetchFailed };
  }
  return { sha: null, baseFetchFailed };
}

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

// packages/hermes-engineering/src/runners/types.ts
import { spawn, spawnSync } from "node:child_process";
var MAX_CAPTURE_BYTES = 64 * 1024 * 1024;
var killProcessTree = (pid) => {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore"
    });
    return;
  }
  try {
    process.kill(-pid, "SIGKILL");
  } catch {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
    }
  }
};
var appendBounded = (chunks, chunk, captured) => {
  if (captured.bytes >= MAX_CAPTURE_BYTES) return;
  const remaining = MAX_CAPTURE_BYTES - captured.bytes;
  const kept = chunk.length <= remaining ? chunk : chunk.subarray(0, remaining);
  chunks.push(kept);
  captured.bytes += kept.length;
};
var NodeProcessRunner = class {
  async run(invocation, timeoutMs) {
    const started = Date.now();
    return await new Promise((resolve9) => {
      const stdout = [];
      const stderr = [];
      const stdoutSize = { bytes: 0 };
      const stderrSize = { bytes: 0 };
      let timedOut = false;
      let spawnError;
      let settled = false;
      const child = spawn(invocation.executable, [...invocation.args], {
        cwd: invocation.cwd,
        detached: process.platform !== "win32",
        env: invocation.env ?? process.env,
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true
      });
      child.stdout.on(
        "data",
        (chunk) => appendBounded(stdout, chunk, stdoutSize)
      );
      child.stderr.on(
        "data",
        (chunk) => appendBounded(stderr, chunk, stderrSize)
      );
      child.on("error", (error) => {
        spawnError = error;
      });
      const timer = setTimeout(() => {
        timedOut = true;
        if (child.pid !== void 0) killProcessTree(child.pid);
      }, timeoutMs);
      timer.unref();
      const finish = (exitCode) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        const result = {
          exitCode,
          stdout: Buffer.concat(stdout).toString("utf8"),
          stderr: Buffer.concat(stderr).toString("utf8"),
          timedOut,
          durationMs: Date.now() - started
        };
        if (spawnError !== void 0) result.error = spawnError.message;
        resolve9(result);
      };
      child.on("close", finish);
    });
  }
};

// packages/hermes-engineering/src/handlers/build-test.ts
var PACKAGE_MANAGERS = /* @__PURE__ */ new Set([
  "npm",
  "pnpm",
  "yarn",
  "bun"
]);
var COMPILE_OR_IMPORT_RE = /(?:Cannot find module|Could not resolve|failed to (?:load|resolve)|SyntaxError|TypeError:.*(?:import|export)|TS\d{4}:|Transform failed|RollupError|ERR_MODULE_NOT_FOUND|No test suite found|test suite failed to run)/i;
var OUTPUT_LIMIT = 8e3;
function discoverBuildTestPlan(workspace, files) {
  const root = realpathSync2(workspace);
  let declaredManager;
  try {
    const manifest = JSON.parse(
      readFileSync3(resolve3(root, "package.json"), "utf8")
    );
    if (typeof manifest.packageManager === "string") {
      declaredManager = manifest.packageManager.split("@")[0];
    }
  } catch {
    return null;
  }
  const alternateLock = [
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb"
  ].some((name) => existsSync3(resolve3(root, name)));
  if (declaredManager !== void 0 && declaredManager !== "npm" || declaredManager === void 0 && alternateLock && !existsSync3(resolve3(root, "package-lock.json"))) {
    return null;
  }
  const globs = readWorkspaceGlobs(root);
  if (globs.length > 0 && hasUnmodeledWorkspaceGlob(globs)) return null;
  let packages = readWorkspacePackages(root);
  let affected;
  if (globs.length === 0) {
    const rootPackage = readRootPackage(root);
    if (rootPackage === null) return null;
    packages = [rootPackage];
    affected = files.length > 0 ? ["."] : [];
  } else {
    if (packages.length === 0) return null;
    affected = affectedWorkspaces(
      files.map((file) => file.path),
      globs
    );
  }
  const byDir = new Map(packages.map((entry) => [entry.dir, entry]));
  if (affected.some((dir) => !byDir.has(dir))) return null;
  const commands = [];
  for (const dir of buildSetFor(affected, packages)) {
    if (!byDir.get(dir)?.scripts.includes("build")) continue;
    commands.push({
      phase: "build",
      executable: "npm",
      args: dir === "." ? ["run", "build"] : ["run", "build", "--workspace", dir],
      cwd: "."
    });
  }
  for (const dir of affected) {
    if (!byDir.get(dir)?.scripts.includes("test")) continue;
    const testFiles = files.filter((file) => file.kind === "test").map((file) => file.path).filter(
      (path) => dir === "." || path === dir || path.startsWith(`${dir}/`)
    );
    commands.push({
      phase: "test",
      executable: "npm",
      args: dir === "." ? ["run", "test"] : ["run", "test", "--workspace", dir],
      cwd: ".",
      ...testFiles.length > 0 ? { testFiles } : {}
    });
  }
  return { packageManager: "npm", commands };
}
var asRecord = (value, label) => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value;
};
var within = (root, candidate) => {
  const rel = relative2(root, candidate);
  return rel === "" || !rel.startsWith(`..${sep3}`) && rel !== ".." && !isAbsolute3(rel);
};
var validatePlanPath = (request, value) => {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError("planPath must be a non-empty string");
  }
  const artifactRoot = realpathSync2(request.artifactRoot);
  const planPath = realpathSync2(resolve3(value));
  if (!within(artifactRoot, planPath))
    throw new TypeError("planPath must be inside artifactRoot");
  if (!lstatSync3(planPath).isFile())
    throw new TypeError("planPath must be a file");
  return planPath;
};
var parseInput = (request) => {
  const input = asRecord(request.input, "input");
  const unknown = Object.keys(input).find(
    (key) => !["planPath", "timeoutMs"].includes(key)
  );
  if (unknown !== void 0)
    throw new TypeError(`unknown build-test input field: ${unknown}`);
  const timeoutMs = input.timeoutMs ?? 3e5;
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 6e5) {
    throw new TypeError("timeoutMs must be an integer between 1 and 600000");
  }
  return {
    planPath: validatePlanPath(request, input.planPath),
    timeoutMs
  };
};
var strings = (value, label) => {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new TypeError(`${label} must be an array of strings`);
  }
  if (value.length > 64 || value.some((item) => item.length > 4096 || item.includes("\0"))) {
    throw new TypeError(
      `${label} is too large or contains an invalid argument`
    );
  }
  return [...value];
};
var parseRecordedPlan = (planPath, workspace) => {
  const plan = asRecord(
    JSON.parse(readFileSync3(planPath, "utf8")),
    "plan"
  );
  const hermes = asRecord(plan.hermes, "plan.hermes");
  const buildTest = asRecord(hermes.buildTest, "plan.hermes.buildTest");
  if (typeof buildTest.packageManager !== "string" || !PACKAGE_MANAGERS.has(buildTest.packageManager)) {
    throw new TypeError(
      "plan.hermes.buildTest.packageManager is not supported"
    );
  }
  if (!Array.isArray(buildTest.commands) || buildTest.commands.length > 64) {
    throw new TypeError(
      "plan.hermes.buildTest.commands must contain at most 64 recorded commands"
    );
  }
  const packageManager = buildTest.packageManager;
  const commands = buildTest.commands.map((raw, index) => {
    const command = asRecord(raw, `command ${index}`);
    if (command.phase !== "build" && command.phase !== "test") {
      throw new TypeError(`command ${index} phase must be build or test`);
    }
    if (command.executable !== packageManager) {
      throw new TypeError(
        `command ${index} must use the recorded package-manager executable`
      );
    }
    const args = strings(command.args, `command ${index} args`);
    if (args.length === 0)
      throw new TypeError(`command ${index} has no arguments`);
    if (args[0] !== "run" && !(packageManager === "npm" && args[0] === "test")) {
      throw new TypeError(
        `command ${index} must invoke a recorded package script, not an arbitrary executable`
      );
    }
    if (typeof command.cwd !== "string" || command.cwd.length === 0 || isAbsolute3(command.cwd)) {
      throw new TypeError(`command ${index} cwd must be repository-relative`);
    }
    const cwd = resolve3(workspace, command.cwd);
    if (!within(workspace, cwd))
      throw new TypeError(`command ${index} cwd escapes the workspace`);
    const canonicalCwd = realpathSync2(cwd);
    if (!within(workspace, canonicalCwd) || !lstatSync3(canonicalCwd).isDirectory()) {
      throw new TypeError(`command ${index} cwd must be a workspace directory`);
    }
    const testFiles = command.testFiles === void 0 ? [] : strings(command.testFiles, `command ${index} testFiles`);
    for (const file of testFiles) {
      if (isAbsolute3(file) || !within(workspace, resolve3(workspace, file))) {
        throw new TypeError(`command ${index} test file escapes the workspace`);
      }
    }
    return {
      phase: command.phase,
      executable: packageManager,
      args,
      cwd: relative2(workspace, canonicalCwd) || ".",
      testFiles
    };
  });
  return { packageManager, commands };
};
var boundedOutput = (run) => {
  const value = `${run.stdout}${run.stderr}`.trim();
  if (value.length <= OUTPUT_LIMIT) return value;
  return `${value.slice(0, 2e3)}
... [output truncated] ...
${value.slice(-6e3)}`;
};
var structuredVitestFiles = (stdout) => {
  const start = stdout.indexOf("{");
  if (start < 0) return [];
  try {
    const parsed = JSON.parse(stdout.slice(start));
    if (!Array.isArray(parsed.testResults)) return [];
    return [
      ...new Set(
        parsed.testResults.map((result) => result.name).filter(
          (name) => typeof name === "string" && name.length > 0
        )
      )
    ];
  } catch {
    return [];
  }
};
var classifyCommand = (command, run) => {
  if (run.timedOut) {
    return {
      outcome: "inconclusive",
      detail: `command timed out after ${run.durationMs}ms`
    };
  }
  if (run.error || run.exitCode === null) {
    return {
      outcome: "inconclusive",
      detail: `command could not run${run.error ? `: ${run.error}` : ""}`
    };
  }
  if (run.exitCode === 0)
    return { outcome: "passed", detail: "command exited 0" };
  const combined = `${run.stdout}
${run.stderr}`;
  if (command.phase === "build") {
    return {
      outcome: "inconclusive",
      detail: "the build failed; compilation and infrastructure failures are not a verified test finding"
    };
  }
  const structuredFiles = structuredVitestFiles(run.stdout);
  const evidenceFiles = structuredFiles.length > 0 ? structuredFiles : command.testFiles;
  if (evidenceFiles.length > 0) {
    const classified = classifyProbeRun(
      run.exitCode,
      run.stdout,
      evidenceFiles,
      run.stderr
    );
    if (classified.some((test) => test.verdict === "gated")) {
      return {
        outcome: "failed",
        detail: "one or more test assertions failed"
      };
    }
    return {
      outcome: "inconclusive",
      detail: classified.map((test) => test.detail).join("; ")
    };
  }
  if (COMPILE_OR_IMPORT_RE.test(combined)) {
    return {
      outcome: "inconclusive",
      detail: "test collection, compilation, or import failed"
    };
  }
  return {
    outcome: "inconclusive",
    detail: "the test command failed without structured per-file assertion evidence"
  };
};
async function runBuildTest(request, processes = new NodeProcessRunner()) {
  const input = parseInput(request);
  const workspace = realpathSync2(request.workspace);
  let recorded;
  try {
    recorded = parseRecordedPlan(input.planPath, workspace);
  } catch (cause) {
    return {
      status: "inconclusive",
      output: { packageManager: null, commands: [] },
      diagnostics: [
        {
          code: "invalid_build_plan",
          message: cause instanceof Error ? cause.message : String(cause)
        }
      ]
    };
  }
  const commands = [];
  for (const command of recorded.commands) {
    const run = await processes.run(
      {
        executable: command.executable,
        args: command.args,
        cwd: resolve3(workspace, command.cwd),
        env: {
          ...process.env,
          CI: "1",
          NO_COLOR: "1",
          npm_config_yes: "true",
          QWEN_SKIP_PREPARE: "1"
        }
      },
      input.timeoutMs
    );
    const classification = classifyCommand(command, run);
    commands.push({
      phase: command.phase,
      executable: command.executable,
      args: [...command.args],
      cwd: command.cwd,
      exitCode: run.exitCode,
      timedOut: run.timedOut,
      durationMs: run.durationMs,
      outcome: classification.outcome,
      detail: classification.detail,
      output: boundedOutput(run)
    });
  }
  const status = commands.some(
    (command) => command.outcome === "failed"
  ) ? "failed" : commands.some((command) => command.outcome === "inconclusive") ? "inconclusive" : "passed";
  return {
    status,
    output: { packageManager: recorded.packageManager, commands },
    diagnostics: []
  };
}

// packages/hermes-engineering/src/handlers/capture-target.ts
import { execFileSync as execFileSync2 } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  chmodSync,
  closeSync,
  existsSync as existsSync4,
  fsyncSync,
  lstatSync as lstatSync4,
  openSync,
  readFileSync as readFileSync4,
  realpathSync as realpathSync3,
  renameSync,
  unlinkSync,
  writeFileSync as writeFileSync2
} from "node:fs";
import {
  basename,
  dirname as dirname2,
  isAbsolute as isAbsolute4,
  join as join5,
  relative as relative3,
  resolve as resolve5,
  sep as sep4
} from "node:path";

// packages/hermes-engineering/src/protocol.ts
import { resolve as resolve4 } from "node:path";
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
    workspace: resolve4(requiredString(value, "workspace")),
    artifactRoot: resolve4(requiredString(value, "artifactRoot")),
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
  const rel = relative3(root, candidate);
  return rel === ".." || rel.startsWith(`..${sep4}`) || isAbsolute4(rel);
};
var validatedDirectory = (path, label) => {
  let stat;
  try {
    stat = lstatSync4(path);
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
  return realpathSync3(path);
};
var validatedArtifactRoot = (path) => {
  const root = validatedDirectory(path, "artifactRoot");
  if (process.platform !== "win32" && (lstatSync4(root).mode & 63) !== 0) {
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
  const repoRoot = realpathSync3(rawRoot);
  if (escaped(repoRoot, canonicalWorkspace)) {
    throw new CaptureTargetError(
      "invalid_repository",
      "workspace is outside the resolved Git repository"
    );
  }
  return { repoRoot, workspace: canonicalWorkspace };
};
var validateRelativeFile = (workspace, repoRoot, path) => {
  if (path.length === 0 || isAbsolute4(path) || path.includes("\0")) {
    throw new CaptureTargetError(
      "invalid_target",
      "file path must be a non-empty repository-relative path"
    );
  }
  const absolute = resolve5(workspace, path);
  if (escaped(repoRoot, absolute) || absolute === repoRoot) {
    throw new CaptureTargetError(
      "invalid_target",
      "file path escapes the repository"
    );
  }
  let existing = absolute;
  while (!existsSync4(existing) && existing !== repoRoot)
    existing = dirname2(existing);
  let canonicalExisting;
  try {
    canonicalExisting = realpathSync3(existing);
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
  return relative3(repoRoot, absolute).split(sep4).join("/");
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
  return join5(dirname2(repoRoot), `${WORKTREE_PREFIX}${suffix}`);
};
var addWorktree = (repoRoot, worktreePath, headRef) => {
  if (existsSync4(worktreePath)) {
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
  const absolute = resolve5(root, path);
  if (escaped(root, absolute)) return 0;
  try {
    const stat = lstatSync4(absolute);
    if (!stat.isFile()) return 0;
    const contents = readFileSync4(absolute);
    if (contents.length === 0) return 0;
    let lines = 0;
    for (const byte of contents) if (byte === 10) lines++;
    return contents[contents.length - 1] === 10 ? lines : lines + 1;
  } catch {
    return 0;
  }
};
var atomicWrite = (root, name, contents) => {
  const destination = join5(root, name);
  if (dirname2(destination) !== root) {
    throw new CaptureTargetError(
      "invalid_artifact",
      "artifact path escapes run root"
    );
  }
  const temporary = join5(
    root,
    `.${name}.${randomBytes(12).toString("hex")}.tmp`
  );
  let descriptor;
  try {
    descriptor = openSync(temporary, "wx", 384);
    writeFileSync2(descriptor, contents);
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
        skippedFiles,
        buildTest: discoverBuildTestPlan(
          captured.postImageRoot,
          reportBase.files
        )
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
  if (!existsSync4(worktreePath)) return;
  const stat = lstatSync4(worktreePath);
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "worktree path is not a real directory"
    );
  }
  const canonical = realpathSync3(worktreePath);
  if (canonical !== resolve5(worktreePath)) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "worktree path is not canonical"
    );
  }
  const gitEntry = lstatSync4(join5(canonical, ".git"));
  if (!gitEntry.isFile() || gitEntry.isSymbolicLink()) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "refusing to remove a source checkout as a disposable worktree"
    );
  }
  const common = gitText(canonical, "rev-parse", "--git-common-dir");
  const commonDirectory = realpathSync3(resolve5(canonical, common));
  execFileSync2(
    "git",
    [
      `--git-dir=${commonDirectory}`,
      "worktree",
      "remove",
      "--force",
      canonical
    ],
    gitOptions(dirname2(canonical))
  );
  gitTextOptional(
    dirname2(canonical),
    `--git-dir=${commonDirectory}`,
    "worktree",
    "prune"
  );
};

// packages/hermes-engineering/src/handlers/test-efficacy.ts
import { execFileSync as execFileSync3, spawnSync as spawnSync2 } from "node:child_process";
import { createHash as createHash2 } from "node:crypto";
import {
  existsSync as existsSync7,
  lstatSync as lstatSync5,
  readFileSync as readFileSync7,
  realpathSync as realpathSync6,
  rmSync as rmSync2
} from "node:fs";
import { isAbsolute as isAbsolute7, join as join6, relative as relative6, resolve as resolve8, sep as sep7 } from "node:path";

// packages/hermes-engineering/src/runners/pytest.ts
import { existsSync as existsSync5, readFileSync as readFileSync5, realpathSync as realpathSync4 } from "node:fs";
import { dirname as dirname3, isAbsolute as isAbsolute5, relative as relative4, resolve as resolve6, sep as sep5 } from "node:path";
var CONFIG_NAMES = ["pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"];
var ENV_NAMES = /* @__PURE__ */ new Set([
  "PATH",
  "HOME",
  "USERPROFILE",
  "HOMEDRIVE",
  "HOMEPATH",
  "APPDATA",
  "LOCALAPPDATA",
  "PROGRAMDATA",
  "SYSTEMROOT",
  "WINDIR",
  "COMSPEC",
  "PATHEXT",
  "TMP",
  "TEMP",
  "TMPDIR",
  "LANG",
  "LANGUAGE"
]);
var within2 = (root, candidate) => {
  const rel = relative4(root, candidate);
  return rel === "" || !rel.startsWith(`..${sep5}`) && rel !== ".." && !isAbsolute5(rel);
};
var findModuleRoot = () => {
  let cursor = resolve6(import.meta.dirname);
  for (; ; ) {
    if (existsSync5(
      resolve6(cursor, "hermes_cli/engineering_review/pytest_probe.py")
    )) {
      return cursor;
    }
    const parent = dirname3(cursor);
    if (parent === cursor) return resolve6(import.meta.dirname);
    cursor = parent;
  }
};
var defaultPython = () => {
  const root = findModuleRoot();
  const names = process.platform === "win32" ? [".venv/Scripts/python.exe", "venv/Scripts/python.exe"] : [".venv/bin/python", "venv/bin/python"];
  for (const name of names) {
    const candidate = resolve6(root, name);
    if (existsSync5(candidate)) return candidate;
  }
  let cursor = root;
  for (; ; ) {
    const candidate = resolve6(
      cursor,
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python"
    );
    if (existsSync5(candidate)) return candidate;
    const parent = dirname3(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
  return process.platform === "win32" ? "python" : "python3";
};
var workspacePython = (workspace) => {
  const names = process.platform === "win32" ? [".venv/Scripts/python.exe", "venv/Scripts/python.exe"] : [".venv/bin/python", "venv/bin/python"];
  for (const name of names) {
    const candidate = resolve6(workspace, name);
    if (existsSync5(candidate)) return candidate;
  }
  return null;
};
var probeEnvironment = () => {
  const env = {};
  for (const [name, value] of Object.entries(process.env)) {
    if (value !== void 0 && (ENV_NAMES.has(name) || name.startsWith("LC_"))) {
      env[name] = value;
    }
  }
  env.CI = "1";
  env.NO_COLOR = "1";
  env.PYTHONDONTWRITEBYTECODE = "1";
  env.PYTHONSAFEPATH = "1";
  env.PYTHONPATH = findModuleRoot();
  return env;
};
var manifestSignalsPytest = (workspace) => {
  for (const name of CONFIG_NAMES) {
    try {
      const contents = readFileSync5(resolve6(workspace, name), "utf8");
      if (name === "pytest.ini" || /\[tool\.pytest\.ini_options\]/.test(contents) || /(?:^|\n)\[pytest\](?:\n|$)/.test(contents)) {
        return true;
      }
    } catch {
    }
  }
  for (const name of ["requirements.txt", "requirements-dev.txt"]) {
    try {
      if (/^\s*pytest(?:\b|[<=>~!])/m.test(
        readFileSync5(resolve6(workspace, name), "utf8")
      )) {
        return true;
      }
    } catch {
    }
  }
  return false;
};
var safeRelativeFile = (workspace, raw) => {
  if (raw.length === 0 || raw.includes("\0") || isAbsolute5(raw)) {
    throw new Error("test path must be repository-relative");
  }
  const normalized = raw.split("\\").join("/");
  if (normalized.split("/").some((part) => part === "." || part === "..")) {
    throw new Error("test path contains an unsafe segment");
  }
  const root = resolve6(workspace);
  const target = resolve6(root, normalized);
  if (!within2(root, target)) throw new Error("test path escapes the workspace");
  if (existsSync5(target) && !within2(root, realpathSync4(target))) {
    throw new Error("test path resolves outside the workspace");
  }
  return normalized;
};
var asStringArray = (value, field) => {
  if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) {
    throw new Error(`pytest probe ${field} is not a string array`);
  }
  return value;
};
var parseProbeResult = (stdout) => {
  let parsed;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    throw new Error("pytest probe produced no parseable JSON");
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("pytest probe result is not an object");
  }
  const record = parsed;
  if (record.command !== "collect" && record.command !== "run") {
    throw new Error("pytest probe returned an invalid command");
  }
  if (typeof record.outcome !== "string") {
    throw new Error("pytest probe returned no outcome");
  }
  const outcomes = /* @__PURE__ */ new Set([
    "collected",
    "passed",
    "assertion_failed",
    "collection_or_import_error",
    "setup_or_teardown_error",
    "internal_error",
    "interrupted",
    "no_tests_executed",
    "probe_error"
  ]);
  if (!outcomes.has(record.outcome)) {
    throw new Error("pytest probe returned an invalid outcome");
  }
  if (!Number.isSafeInteger(record.pytestExitCode)) {
    throw new Error("pytest probe returned no valid exit code");
  }
  return {
    command: record.command,
    outcome: record.outcome,
    pytestExitCode: record.pytestExitCode,
    files: asStringArray(record.files, "files"),
    collectionErrors: asStringArray(
      record.collectionErrors ?? [],
      "collectionErrors"
    ),
    ...typeof record.passed === "number" ? { passed: record.passed } : {},
    ...typeof record.failedAssertions === "number" ? { failedAssertions: record.failedAssertions } : {},
    ...typeof record.skipped === "number" ? { skipped: record.skipped } : {},
    ...typeof record.error === "string" ? { error: record.error } : {}
  };
};
var failedRun = (run, message) => ({
  ...run,
  exitCode: null,
  error: message
});
var PytestRunner = class {
  constructor(processes = new NodeProcessRunner(), python) {
    this.processes = processes;
    this.python = python;
  }
  processes;
  python;
  id = "pytest";
  pythonFor(workspace) {
    return this.python ?? workspacePython(workspace) ?? defaultPython();
  }
  async detect(workspace, plan) {
    if (manifestSignalsPytest(workspace)) return "yes";
    return plan.files.some(
      (file) => file.kind === "test" && /(?:^|\/)(?:test_[^/]+|[^/]+_test)\.py$/.test(file.path)
    ) ? "ambiguous" : "no";
  }
  async collectedFiles(workspace) {
    const root = resolve6(workspace);
    const run = await this.processes.run(
      {
        executable: this.pythonFor(root),
        args: [
          "-m",
          "hermes_cli.engineering_review.pytest_probe",
          "collect",
          "--root",
          root
        ],
        cwd: root,
        env: probeEnvironment()
      },
      6e4
    );
    if (run.timedOut) throw new Error("pytest collection timed out");
    if (run.error)
      throw new Error(`pytest collection could not start: ${run.error}`);
    if (run.exitCode !== 0) {
      throw new Error(`pytest collection probe exited ${run.exitCode}`);
    }
    const result = parseProbeResult(run.stdout);
    if (result.command !== "collect") {
      throw new Error("pytest collection probe returned a run result");
    }
    if (result.outcome === "internal_error" || result.outcome === "interrupted" || result.outcome === "probe_error") {
      throw new Error(`pytest collection was inconclusive: ${result.outcome}`);
    }
    if (result.outcome === "collection_or_import_error") {
      const specificErrors = result.collectionErrors.filter(
        (file) => /(?:^|\/)(?:test_[^/]+|[^/]+_test)\.py$/.test(file)
      );
      if (specificErrors.length === 0 || specificErrors.length !== result.collectionErrors.length) {
        throw new Error("pytest collection failed outside a test module");
      }
    }
    return new Set(
      [...result.files, ...result.collectionErrors].map(
        (file) => safeRelativeFile(root, file)
      )
    );
  }
  async runFile(workspace, relativePath, timeoutMs) {
    const root = resolve6(workspace);
    const file = safeRelativeFile(root, relativePath);
    const run = await this.processes.run(
      {
        executable: this.pythonFor(root),
        args: [
          "-m",
          "hermes_cli.engineering_review.pytest_probe",
          "run",
          "--root",
          root,
          "--file",
          file
        ],
        cwd: root,
        env: probeEnvironment()
      },
      timeoutMs
    );
    if (run.timedOut || run.error || run.exitCode !== 0) return run;
    let result;
    try {
      result = parseProbeResult(run.stdout);
    } catch (cause) {
      return failedRun(
        run,
        cause instanceof Error ? cause.message : String(cause)
      );
    }
    if (result.command !== "run" || result.outcome === "collected") {
      return failedRun(run, "pytest run probe returned a collection result");
    }
    const structured = {
      framework: "pytest",
      outcome: result.outcome,
      passed: result.passed ?? 0,
      failedAssertions: result.failedAssertions ?? 0,
      skipped: result.skipped ?? 0
    };
    const error = result.outcome === "probe_error" ? result.error : void 0;
    return {
      ...run,
      exitCode: result.outcome === "passed" ? 0 : Math.max(result.pytestExitCode, 1),
      structured,
      ...error === void 0 ? {} : { error }
    };
  }
};

// packages/hermes-engineering/src/runners/vitest.ts
import { existsSync as existsSync6, readFileSync as readFileSync6, realpathSync as realpathSync5 } from "node:fs";
import { dirname as dirname4, isAbsolute as isAbsolute6, relative as relative5, resolve as resolve7, sep as sep6 } from "node:path";
var CONFIG_NAMES2 = [
  "vitest.config.ts",
  "vitest.config.js",
  "vitest.config.mts",
  "vitest.config.mjs"
];
var within3 = (root, candidate) => {
  const rel = relative5(root, candidate);
  return rel === "" || !rel.startsWith(`..${sep6}`) && rel !== ".." && !isAbsolute6(rel);
};
var manifestSignalsVitest = (workspace) => {
  try {
    const parsed = JSON.parse(
      readFileSync6(resolve7(workspace, "package.json"), "utf8")
    );
    if ("vitest" in (parsed.dependencies ?? {})) return true;
    if ("vitest" in (parsed.devDependencies ?? {})) return true;
    return Object.values(parsed.scripts ?? {}).some(
      (script) => typeof script === "string" && /(^|\s)vitest(?:\s|$)/.test(script)
    );
  } catch {
    return false;
  }
};
var planSignalsVitest = (workspace, plan) => {
  const root = resolve7(workspace);
  for (const file of plan.files) {
    if (file.kind !== "test" || isAbsolute6(file.path)) continue;
    const absolute = resolve7(root, file.path);
    if (!within3(root, absolute)) continue;
    let cursor = dirname4(absolute);
    for (; ; ) {
      if (CONFIG_NAMES2.some((name) => existsSync6(resolve7(cursor, name))) || manifestSignalsVitest(cursor)) {
        return true;
      }
      if (cursor === root) break;
      const parent = dirname4(cursor);
      if (parent === cursor || !within3(root, parent)) break;
      cursor = parent;
    }
  }
  return false;
};
var resolveVitestModule = (workspace) => {
  let cursor = resolve7(workspace);
  for (; ; ) {
    const candidate = resolve7(cursor, "node_modules/vitest/vitest.mjs");
    if (existsSync6(candidate)) return candidate;
    const parent = dirname4(cursor);
    if (parent === cursor) return null;
    cursor = parent;
  }
};
var parseCollectedFiles = (workspace, stdout) => {
  const parsed = JSON.parse(stdout);
  if (!Array.isArray(parsed))
    throw new Error("Vitest collection output is not an array");
  const files = /* @__PURE__ */ new Set();
  for (const entry of parsed) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry))
      continue;
    const file = entry.file;
    if (typeof file !== "string") continue;
    const absolute = resolve7(workspace, file);
    if (!within3(resolve7(workspace), absolute)) continue;
    files.add(relative5(resolve7(workspace), absolute).split(sep6).join("/"));
  }
  return files;
};
var VitestRunner = class {
  constructor(processes = new NodeProcessRunner()) {
    this.processes = processes;
  }
  processes;
  id = "vitest";
  async detect(workspace, plan) {
    const configured = CONFIG_NAMES2.some(
      (name) => existsSync6(resolve7(workspace, name))
    );
    return configured || manifestSignalsVitest(workspace) || planSignalsVitest(workspace, plan) ? "yes" : "no";
  }
  async collectedFiles(workspace) {
    const modulePath = resolveVitestModule(workspace);
    if (modulePath === null)
      throw new Error("a local Vitest installation was not found");
    const run = await this.processes.run(
      {
        executable: process.execPath,
        args: [
          modulePath,
          "list",
          "--filesOnly",
          "--configLoader=runner",
          "--no-cache",
          "--json"
        ],
        cwd: workspace,
        env: { ...process.env, CI: "1", NO_COLOR: "1" }
      },
      6e4
    );
    if (run.timedOut) throw new Error("Vitest collection timed out");
    if (run.error)
      throw new Error(`Vitest collection could not start: ${run.error}`);
    if (run.exitCode !== 0) {
      throw new Error(`Vitest collection failed: ${run.stderr.trim()}`);
    }
    return parseCollectedFiles(workspace, run.stdout);
  }
  async runFile(workspace, relativePath, timeoutMs) {
    if (isAbsolute6(relativePath))
      throw new Error("test path must be repository-relative");
    const target = resolve7(workspace, relativePath);
    if (!within3(resolve7(workspace), target)) {
      throw new Error("test path escapes the workspace");
    }
    if (existsSync6(target) && !within3(resolve7(workspace), realpathSync5(target))) {
      throw new Error("test path resolves outside the workspace");
    }
    const modulePath = resolveVitestModule(workspace);
    if (modulePath === null) {
      return {
        exitCode: null,
        stdout: "",
        stderr: "",
        timedOut: false,
        durationMs: 0,
        error: "a local Vitest installation was not found"
      };
    }
    return await this.processes.run(
      {
        executable: process.execPath,
        args: [
          modulePath,
          "run",
          "--reporter=json",
          "--configLoader=runner",
          "--no-cache",
          relativePath
        ],
        cwd: workspace,
        env: { ...process.env, CI: "1", NO_COLOR: "1" }
      },
      timeoutMs
    );
  }
};

// packages/hermes-engineering/src/handlers/test-efficacy.ts
var asRecord2 = (value) => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("input must be an object");
  }
  return value;
};
var within4 = (root, candidate) => {
  const rel = relative6(root, candidate);
  return rel === "" || !rel.startsWith(`..${sep7}`) && rel !== ".." && !isAbsolute7(rel);
};
var validatedPlanPath = (request, raw) => {
  if (typeof raw !== "string" || raw.length === 0) {
    throw new TypeError("planPath must be a non-empty string");
  }
  const artifactRoot = realpathSync6(request.artifactRoot);
  const path = realpathSync6(resolve8(raw));
  if (!within4(artifactRoot, path)) {
    throw new TypeError("planPath must be inside artifactRoot");
  }
  if (!lstatSync5(path).isFile()) throw new TypeError("planPath must be a file");
  return path;
};
var parseInput2 = (request) => {
  const input = asRecord2(request.input);
  const unknown = Object.keys(input).find(
    (key) => !["planPath", "baseRef", "runner", "timeoutMs"].includes(key)
  );
  if (unknown !== void 0)
    throw new TypeError(`unknown test-efficacy input field: ${unknown}`);
  const planPath = validatedPlanPath(request, input.planPath);
  if (typeof input.baseRef !== "string" || !/^[0-9a-fA-F]{40,64}$/.test(input.baseRef)) {
    throw new TypeError("baseRef must be a full Git object ID");
  }
  if (!["auto", "vitest", "pytest"].includes(input.runner)) {
    throw new TypeError("runner must be auto, vitest, or pytest");
  }
  const timeoutMs = input.timeoutMs ?? 3e5;
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 6e5) {
    throw new TypeError("timeoutMs must be an integer between 1 and 600000");
  }
  return {
    planPath,
    baseRef: input.baseRef,
    runner: input.runner,
    timeoutMs
  };
};
var parsePlan = (path) => {
  const parsed = JSON.parse(readFileSync7(path, "utf8"));
  const record = asRecord2(parsed);
  if (!Array.isArray(record.files))
    throw new TypeError("plan.files must be an array");
  const files = record.files.map((entry) => {
    const file = asRecord2(entry);
    if (typeof file.path !== "string" || typeof file.kind !== "string") {
      throw new TypeError(
        "every plan file requires string path and kind fields"
      );
    }
    return { path: file.path, kind: file.kind };
  });
  return { ...record, files };
};
var emptyOutput = (availableRunners) => ({
  runner: null,
  tests: [],
  unreachable: [],
  gated: [],
  inert: [],
  inconclusive: [],
  availableRunners,
  probeWorktreePath: null,
  cleanupFailure: null
});
async function selectRunner(selection, workspace, plan, runners) {
  if (selection !== "auto") {
    const runner = runners.find((candidate) => candidate.id === selection);
    if (runner === void 0) return { code: "no_runner", available: [] };
    const detected = await runner.detect(workspace, plan);
    return detected === "no" ? { code: "no_runner", available: [] } : { runner, available: [runner.id] };
  }
  const detections = await Promise.all(
    runners.map(async (runner) => ({
      runner,
      detection: await runner.detect(workspace, plan)
    }))
  );
  const yes = detections.filter(({ detection }) => detection === "yes").map(({ runner }) => runner);
  const ambiguous = detections.filter(({ detection }) => detection === "ambiguous").map(({ runner }) => runner);
  const available = [
    ...new Set([...yes, ...ambiguous].map((runner) => runner.id))
  ];
  if (yes.length === 1 && ambiguous.length === 0)
    return { runner: yes[0], available };
  if (yes.length === 0 && ambiguous.length === 0)
    return { code: "no_runner", available };
  return { code: "ambiguous_runner", available };
}
var git2 = (cwd, args) => execFileSync3("git", [...args], {
  cwd,
  encoding: "utf8",
  env: { ...process.env, GIT_TERMINAL_PROMPT: "0" },
  timeout: 12e4,
  maxBuffer: 64 * 1024 * 1024
}).trim();
var existsAtBase = (cwd, baseRef, path) => {
  const result = spawnSync2("git", ["cat-file", "-e", `${baseRef}:${path}`], {
    cwd,
    env: { ...process.env, GIT_TERMINAL_PROMPT: "0" },
    stdio: "ignore",
    timeout: 12e4
  });
  if (result.error !== void 0) throw result.error;
  return result.status === 0;
};
var safeRelativePath = (workspace, path) => {
  if (path.length === 0 || path.includes("\0") || isAbsolute7(path)) {
    throw new Error(`unsafe plan path: ${JSON.stringify(path)}`);
  }
  const normalized = path.split("\\").join("/");
  if (normalized.split("/").some((segment) => segment === ".." || segment === ".")) {
    throw new Error(`unsafe plan path segment: ${JSON.stringify(path)}`);
  }
  const target = resolve8(workspace, normalized);
  if (!within4(resolve8(workspace), target)) {
    throw new Error(
      `plan path escapes the probe worktree: ${JSON.stringify(path)}`
    );
  }
  return normalized;
};
var probeTreePath = (workspace, requestId) => {
  const suffix = createHash2("sha256").update(`${requestId}\0${Date.now()}\0${Math.random()}`).digest("hex").slice(0, 16);
  return join6(workspace, `.hermes-efficacy-${suffix}`);
};
var createProbeTree = (workspace, probeTree) => {
  const head = git2(workspace, ["rev-parse", "HEAD"]);
  git2(workspace, ["worktree", "add", "--detach", probeTree, head]);
};
var revertProduction = (probeTree, baseRef, revert) => {
  const modified = [];
  const added = [];
  for (const rawPath of revert) {
    const path = safeRelativePath(probeTree, rawPath);
    if (existsAtBase(probeTree, baseRef, path)) {
      modified.push(path);
    } else {
      added.push(path);
    }
  }
  if (modified.length > 0)
    git2(probeTree, ["checkout", baseRef, "--", ...modified]);
  for (const path of added) safeRmWithin(probeTree, path);
};
var cleanupProbeTree = (workspace, probeTree) => {
  if (!within4(resolve8(workspace), resolve8(probeTree)) || !probeTree.includes(".hermes-efficacy-")) {
    return "refusing to clean an unrecognized probe worktree";
  }
  let failure = null;
  try {
    git2(workspace, ["worktree", "remove", "--force", probeTree]);
  } catch (cause) {
    failure = cause instanceof Error ? cause.message : String(cause);
  }
  try {
    if (existsSync7(probeTree))
      rmSync2(probeTree, { recursive: true, force: true });
    git2(workspace, ["worktree", "prune"]);
  } catch (cause) {
    failure ??= cause instanceof Error ? cause.message : String(cause);
  }
  return existsSync7(probeTree) ? failure ?? `could not remove ${probeTree}` : null;
};
var inconclusiveForRun = (file, run, phase) => ({
  path: file,
  verdict: "inconclusive",
  detail: run.timedOut ? `${phase} timed out after ${run.durationMs}ms` : `${phase} could not run${run.error ? `: ${run.error}` : ""}`
});
var group = (tests, verdict) => tests.filter((test) => test.verdict === verdict).map((test) => test.path);
async function runTestEfficacy(request, runners = [new VitestRunner(), new PytestRunner()]) {
  const input = parseInput2(request);
  const workspace = realpathSync6(request.workspace);
  const plan = parsePlan(input.planPath);
  const choice = await selectRunner(input.runner, workspace, plan, runners);
  if ("code" in choice) {
    const output2 = emptyOutput(choice.available);
    const choices = choice.available.length > 0 ? choice.available.map((id) => `runner=${id}`).join(", ") : "vitest or pytest";
    return {
      status: "inconclusive",
      output: output2,
      diagnostics: [
        {
          code: choice.code,
          message: choice.code === "ambiguous_runner" ? `multiple test runners apply; retry explicitly with ${choices}` : `no applicable test runner was found; retry explicitly with ${choices}`
        }
      ]
    };
  }
  const runner = choice.runner;
  const workspaceGlobs = readWorkspaceGlobs(workspace);
  const upstreamPlan = planTestEfficacy(plan.files, workspaceGlobs);
  const collectionIsAuthoritative = runner.id === "pytest" || workspaceGlobs.length === 0;
  const rootTests = plan.files.filter((file) => file.kind === "test").map((file) => file.path);
  const planned = collectionIsAuthoritative ? {
    unreachable: [],
    probes: upstreamPlan.revert.length > 0 ? rootTests : [],
    revert: upstreamPlan.revert
  } : upstreamPlan;
  const tests = planned.unreachable.map((path) => ({
    path,
    verdict: "unreachable",
    detail: "the changed test is outside every npm workspace"
  }));
  const scheduled = /* @__PURE__ */ new Set([...planned.unreachable, ...planned.probes]);
  for (const file of plan.files) {
    if (file.kind === "test" && !scheduled.has(file.path)) {
      tests.push({
        path: file.path,
        verdict: "inconclusive",
        detail: "the diff has no production source change to revert, so test efficacy cannot be probed"
      });
    }
  }
  let actualProbeTree = null;
  let cleanupFailure = null;
  let probes = [];
  try {
    if (planned.probes.length > 0) {
      actualProbeTree = probeTreePath(workspace, request.requestId);
      createProbeTree(workspace, actualProbeTree);
    }
    if (planned.probes.length > 0) {
      let collected;
      try {
        collected = await runner.collectedFiles(actualProbeTree ?? workspace);
      } catch (cause) {
        const detail = `test collection failed: ${cause instanceof Error ? cause.message : String(cause)}`;
        tests.push(
          ...planned.probes.map((path) => ({
            path,
            verdict: "inconclusive",
            detail
          }))
        );
        probes = [];
        collected = /* @__PURE__ */ new Set();
      }
      if (tests.length === planned.unreachable.length) {
        probes = planned.probes.filter((path) => {
          if (collected.has(path)) return true;
          tests.push({
            path,
            verdict: "unreachable",
            detail: `${runner.id} did not collect the changed test file`
          });
          return false;
        });
      }
    }
    const baselinePassed = [];
    for (const file of probes) {
      const run = await runner.runFile(
        actualProbeTree ?? workspace,
        file,
        input.timeoutMs
      );
      if (run.timedOut || run.error || run.exitCode === null) {
        tests.push(inconclusiveForRun(file, run, "baseline test run"));
      } else if (run.exitCode !== 0) {
        tests.push({
          path: file,
          verdict: "inconclusive",
          detail: "the changed test does not pass before the production revert"
        });
      } else {
        baselinePassed.push(file);
      }
    }
    if (baselinePassed.length > 0 && planned.revert.length > 0) {
      if (actualProbeTree === null)
        throw new Error("probe worktree was not created");
      revertProduction(actualProbeTree, input.baseRef, planned.revert);
      for (const file of baselinePassed) {
        const run = await runner.runFile(
          actualProbeTree,
          file,
          input.timeoutMs
        );
        if (run.timedOut || run.error || run.exitCode === null) {
          tests.push(inconclusiveForRun(file, run, "revert probe"));
          continue;
        }
        const classifierStdout = run.structured ? JSON.stringify({
          testResults: run.structured.outcome === "passed" || run.structured.outcome === "assertion_failed" ? [
            {
              name: file,
              assertionResults: [
                ...Array.from(
                  { length: run.structured.failedAssertions },
                  () => ({ status: "failed" })
                ),
                ...Array.from(
                  { length: run.structured.passed },
                  () => ({ status: "passed" })
                )
              ]
            }
          ] : []
        }) : run.stdout;
        const classified = classifyProbeRun(
          run.exitCode,
          classifierStdout,
          [file],
          run.stderr
        )[0];
        tests.push({
          path: file,
          verdict: classified?.verdict ?? "inconclusive",
          detail: classified?.detail ?? "the revert probe returned no file classification"
        });
      }
    }
  } catch (cause) {
    const classified = new Set(tests.map((test) => test.path));
    for (const file of planned.probes) {
      if (!classified.has(file)) {
        tests.push({
          path: file,
          verdict: "inconclusive",
          detail: `probe could not run: ${cause instanceof Error ? cause.message : String(cause)}`
        });
      }
    }
  } finally {
    if (actualProbeTree !== null)
      cleanupFailure = cleanupProbeTree(workspace, actualProbeTree);
  }
  if (cleanupFailure !== null) {
    for (const test of tests) {
      if (test.verdict !== "unreachable") {
        test.verdict = "inconclusive";
        test.detail = `probe cleanup failed: ${cleanupFailure}`;
      }
    }
  }
  const output = {
    runner: runner.id,
    tests,
    unreachable: group(tests, "unreachable"),
    gated: group(tests, "gated"),
    inert: group(tests, "inert"),
    inconclusive: group(tests, "inconclusive"),
    availableRunners: choice.available,
    probeWorktreePath: actualProbeTree,
    cleanupFailure
  };
  const status = output.inconclusive.length > 0 ? "inconclusive" : output.unreachable.length > 0 || output.inert.length > 0 ? "failed" : "passed";
  return {
    status,
    output,
    diagnostics: cleanupFailure === null ? [] : [{ code: "cleanup_failed", message: cleanupFailure }]
  };
}

// packages/hermes-engineering/src/handlers/index.ts
async function dispatch(request) {
  if (request.command === "build-test") {
    try {
      const result = await runBuildTest(request);
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status: result.status,
        output: { ...result.output },
        diagnostics: result.diagnostics
      };
    } catch (cause) {
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "inconclusive",
          output: {},
          diagnostics: [
            { code: "invalid_build_test_input", message: cause.message }
          ]
        };
      }
      throw cause;
    }
  }
  if (request.command === "test-efficacy") {
    try {
      const result = await runTestEfficacy(request);
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status: result.status,
        output: { ...result.output },
        diagnostics: result.diagnostics
      };
    } catch (cause) {
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "inconclusive",
          output: {},
          diagnostics: [
            { code: "invalid_test_efficacy_input", message: cause.message }
          ]
        };
      }
      throw cause;
    }
  }
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
if (entrypoint && realpathSync7(fileURLToPath(import.meta.url)) === realpathSync7(entrypoint)) {
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
