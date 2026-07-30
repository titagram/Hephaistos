'use strict'

const fs = require('node:fs')
const path = require('node:path')

const ts = require('typescript')

const SOURCE_EXTENSIONS = new Set(['.cjs', '.js', '.jsx', '.mjs', '.ts', '.tsx'])
const DATA_EXTENSIONS = new Set(['.json', '.jsonl'])
const EXCLUDED_DIRECTORIES = new Set(['build', 'dist', 'node_modules', 'release'])
const TEST_FILE_PATTERN = /\.test\./
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

function parseHtmlTag(source, start) {
  let cursor = start + 1
  let closing = false
  if (source[cursor] === '/') {
    closing = true
    cursor += 1
  }

  while (/\s/.test(source[cursor] ?? '')) cursor += 1
  const nameStart = cursor
  while (cursor < source.length && !/[\s/>]/.test(source[cursor])) cursor += 1
  const name = source.slice(nameStart, cursor).toLowerCase()
  const attributes = []
  let selfClosing = false

  while (cursor < source.length) {
    while (/\s/.test(source[cursor] ?? '')) cursor += 1
    if (source[cursor] === '>') {
      cursor += 1
      break
    }
    if (source[cursor] === '/' && source[cursor + 1] === '>') {
      selfClosing = true
      cursor += 2
      break
    }

    const attributeStart = cursor
    while (cursor < source.length && !/[\s=/>]/.test(source[cursor])) cursor += 1
    const attributeName = source.slice(attributeStart, cursor).toLowerCase()
    if (!attributeName) {
      cursor += 1
      continue
    }

    while (/\s/.test(source[cursor] ?? '')) cursor += 1
    let value = null
    if (source[cursor] === '=') {
      cursor += 1
      while (/\s/.test(source[cursor] ?? '')) cursor += 1
      const quote = source[cursor]
      if (quote === '"' || quote === "'") {
        cursor += 1
        const valueStart = cursor
        while (cursor < source.length && source[cursor] !== quote) cursor += 1
        value = source.slice(valueStart, cursor)
        if (source[cursor] === quote) cursor += 1
      } else {
        const valueStart = cursor
        while (cursor < source.length && !/[\s>]/.test(source[cursor])) cursor += 1
        value = source.slice(valueStart, cursor)
      }
    }
    attributes.push({ name: attributeName, value })
  }

  return { attributes, closing, end: cursor, name, selfClosing }
}

function skipRawTextElement(source, lowerSource, start, name) {
  let candidate = lowerSource.indexOf(`</${name}`, start)
  while (candidate !== -1) {
    const closingTag = parseHtmlTag(source, candidate)
    if (closingTag.closing && closingTag.name === name) return closingTag.end
    candidate = lowerSource.indexOf(`</${name}`, candidate + 2)
  }
  return source.length
}

function extractHtmlValues(source) {
  const values = []
  const lowerSource = source.toLowerCase()
  let cursor = 0

  while (cursor < source.length) {
    if (source.startsWith('<!--', cursor)) {
      const commentEnd = source.indexOf('-->', cursor + 4)
      cursor = commentEnd === -1 ? source.length : commentEnd + 3
      continue
    }

    if (source[cursor] === '<') {
      if (source[cursor + 1] === '!' || source[cursor + 1] === '?') {
        const declarationEnd = source.indexOf('>', cursor + 2)
        cursor = declarationEnd === -1 ? source.length : declarationEnd + 1
        continue
      }

      const tag = parseHtmlTag(source, cursor)
      if (!tag.name) {
        cursor += 1
        continue
      }

      if (!tag.closing) {
        for (const attribute of tag.attributes) {
          if (attribute.name === 'title' && attribute.value !== null) {
            values.push(decodeHtmlText(attribute.value))
          }
        }
      }

      cursor = tag.end
      if (!tag.closing && !tag.selfClosing && (tag.name === 'script' || tag.name === 'style')) {
        cursor = skipRawTextElement(source, lowerSource, cursor, tag.name)
      }
      continue
    }

    const textEnd = source.indexOf('<', cursor)
    const end = textEnd === -1 ? source.length : textEnd
    const value = decodeHtmlText(source.slice(cursor, end)).trim()
    if (value) values.push(value)
    cursor = end
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
    if (!TEST_FILE_PATTERN.test(path.basename(resolved)) && isAuditedFile(resolved)) {
      files.add(resolved)
    }
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
