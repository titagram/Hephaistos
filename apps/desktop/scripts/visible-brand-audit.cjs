'use strict'

const fs = require('node:fs')
const path = require('node:path')

const ts = require('typescript')

const SOURCE_EXTENSIONS = new Set(['.cjs', '.js', '.jsx', '.mjs', '.ts', '.tsx'])
const DATA_EXTENSIONS = new Set(['.json', '.jsonl'])
const EXCLUDED_DIRECTORIES = new Set(['build', 'dist', 'node_modules', 'release'])
const TEST_FILE_PATTERN = /\.test\.[^.]+$/
const BANNED_LEGACY = [/\bHermes\b/g, /hermes-(?:chan|san)/g]

const ALLOWED_LEGACY = [
  /\bHERMES_[A-Z0-9_]+\b/,
  /^X-Hermes-Session-Token$/,
  /^hermes:/,
  /^hermes:\/\//,
  /~\/\.hermes\b/,
  /\bHermes[A-Z][A-Za-z0-9_]*\b/,
  /\bHermes\.(?:exe|app)\b/,
  /\b(?:existing|Windows) Hermes (?:CLI|Python|override)\b/,
  /\bHermes (?:CLI|venv)\b/,
  /^Hosted Hermes & Nous-trained models$/,
  /\bHermes(?:-\d|\s*& Nous-trained models)/i,
  /NousResearch/
]

function regexMatches(pattern, value) {
  const flags = pattern.flags.includes('g') ? pattern.flags : `${pattern.flags}g`
  const matcher = new RegExp(pattern.source, flags)
  const matches = []
  let match
  while ((match = matcher.exec(value)) !== null) {
    matches.push({ start: match.index, end: match.index + match[0].length })
    if (match[0].length === 0) matcher.lastIndex += 1
  }
  return matches
}

function containsDisallowedLegacy(value) {
  const allowedSpans = ALLOWED_LEGACY.flatMap(pattern => regexMatches(pattern, value))
  return BANNED_LEGACY.flatMap(pattern => regexMatches(pattern, value)).some(
    banned =>
      !allowedSpans.some(allowed => allowed.start <= banned.start && allowed.end >= banned.end)
  )
}

function isPropertyName(node) {
  const parent = node.parent
  return Boolean(parent && 'name' in parent && parent.name === node)
}

function isModuleSpecifier(node) {
  const parent = node.parent
  return Boolean(
    parent &&
      ((ts.isImportDeclaration(parent) && parent.moduleSpecifier === node) ||
        (ts.isExportDeclaration(parent) && parent.moduleSpecifier === node) ||
        (ts.isExternalModuleReference(parent) && parent.expression === node))
  )
}

function sourceScriptKind(file) {
  switch (path.extname(file)) {
    case '.js':
    case '.cjs':
    case '.mjs':
      return ts.ScriptKind.JS
    case '.jsx':
      return ts.ScriptKind.JSX
    case '.tsx':
      return ts.ScriptKind.TSX
    default:
      return ts.ScriptKind.TS
  }
}

function extractSourceValues(file, source) {
  const sourceFile = ts.createSourceFile(
    file,
    source,
    ts.ScriptTarget.Latest,
    true,
    sourceScriptKind(file)
  )
  const values = []

  function visit(node) {
    if (ts.isStringLiteral(node)) {
      if (!isPropertyName(node) && !isModuleSpecifier(node)) {
        values.push(node.text)
      }
    } else if (ts.isNoSubstitutionTemplateLiteral(node)) {
      values.push(node.text)
    } else if (ts.isTemplateExpression(node)) {
      values.push(node.head.text)
      for (const span of node.templateSpans) values.push(span.literal.text)
    } else if (ts.isJsxText(node)) {
      const value = node.getText(sourceFile).trim()
      if (value) values.push(value)
    }

    ts.forEachChild(node, visit)
  }

  visit(sourceFile)
  return values
}

function collectJsonValues(value, values) {
  if (typeof value === 'string') {
    values.push(value)
    return
  }
  if (Array.isArray(value)) {
    for (const item of value) collectJsonValues(item, values)
    return
  }
  if (value && typeof value === 'object') {
    for (const item of Object.values(value)) collectJsonValues(item, values)
  }
}

function extractDataValues(file, source) {
  const values = []
  if (path.extname(file) === '.jsonl') {
    for (const line of source.split(/\r?\n/)) {
      if (line.trim()) collectJsonValues(JSON.parse(line), values)
    }
  } else {
    collectJsonValues(JSON.parse(source), values)
  }
  return values
}

function decodeHtmlText(value) {
  return value
    .replaceAll('&quot;', '"')
    .replaceAll('&apos;', "'")
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&amp;', '&')
}

function extractHtmlValues(source) {
  const withoutCommentsOrCode = source
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<(?:script|style)\b[^>]*>[\s\S]*?<\/(?:script|style)\s*>/gi, '')
  const values = []

  const titleAttribute = /(?:^|\s)title\s*=\s*(?:"([^"]*)"|'([^']*)')/gi
  let attributeMatch
  while ((attributeMatch = titleAttribute.exec(withoutCommentsOrCode)) !== null) {
    values.push(decodeHtmlText(attributeMatch[1] ?? attributeMatch[2]))
  }

  const textNode = />([^<]+)</g
  let textMatch
  while ((textMatch = textNode.exec(withoutCommentsOrCode)) !== null) {
    const value = decodeHtmlText(textMatch[1]).trim()
    if (value) values.push(value)
  }

  return values
}

function isAuditedFile(file) {
  const extension = path.extname(file)
  return (
    SOURCE_EXTENSIONS.has(extension) ||
    DATA_EXTENSIONS.has(extension) ||
    path.basename(file) === 'index.html'
  )
}

function collectDesktopFiles(desktopRoot) {
  const files = []

  function visit(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        if (!EXCLUDED_DIRECTORIES.has(entry.name)) visit(path.join(directory, entry.name))
        continue
      }

      const file = path.join(directory, entry.name)
      if (entry.isFile() && !TEST_FILE_PATTERN.test(entry.name) && isAuditedFile(file)) {
        files.push(file)
      }
    }
  }

  visit(desktopRoot)
  return files
}

function displayPath(desktopRoot, file) {
  const relative = path.relative(desktopRoot, file)
  return relative && !relative.startsWith(`..${path.sep}`) && relative !== '..' ? relative : file
}

function auditVisibleBrand({ desktopRoot, extraFiles = [] }) {
  const root = path.resolve(desktopRoot)
  const files = new Set(collectDesktopFiles(root).map(file => path.resolve(file)))
  for (const file of extraFiles) {
    const resolved = path.resolve(file)
    if (isAuditedFile(resolved)) files.add(resolved)
  }

  const violations = []
  for (const file of [...files].sort()) {
    const source = fs.readFileSync(file, 'utf8')
    const extension = path.extname(file)
    let values
    if (SOURCE_EXTENSIONS.has(extension)) {
      values = extractSourceValues(file, source)
    } else if (DATA_EXTENSIONS.has(extension)) {
      values = extractDataValues(file, source)
    } else {
      values = extractHtmlValues(source)
    }

    for (const value of values) {
      if (containsDisallowedLegacy(value)) {
        violations.push({ file: displayPath(root, file), value })
      }
    }
  }

  return { ok: violations.length === 0, violations }
}

function main() {
  const result = auditVisibleBrand({ desktopRoot: path.resolve(__dirname, '..') })
  if (!result.ok) {
    console.error(
      `Visible Hades brand audit failed:\n${result.violations
        .map(({ file, value }) => `- ${file}: ${JSON.stringify(value)}`)
        .join('\n')}`
    )
    process.exitCode = 1
    return
  }
  console.log('Visible Hades brand audit passed.')
}

if (require.main === module) {
  main()
}

module.exports = { auditVisibleBrand }
