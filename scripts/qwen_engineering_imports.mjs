#!/usr/bin/env node
/** Extract literal TypeScript module specifiers for the Qwen source slicer. */

import ts from 'typescript';

if (process.argv.length !== 3) {
  throw new Error('usage: qwen_engineering_imports.mjs <relative-source-path>');
}

const filename = process.argv[2];
let source = '';
for await (const chunk of process.stdin) {
  source += chunk;
}

const scriptKind = filename.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
const sourceFile = ts.createSourceFile(
  filename,
  source,
  ts.ScriptTarget.Latest,
  true,
  scriptKind,
);
if (sourceFile.parseDiagnostics.length > 0) {
  throw new Error(`TypeScript parse failure in ${filename}`);
}

const specifiers = [];
const literalText = node => (ts.isStringLiteral(node) ? node.text : null);
const add = node => {
  const text = node && literalText(node);
  if (typeof text === 'string') specifiers.push(text);
};

const visit = node => {
  if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
    add(node.moduleSpecifier);
  } else if (
    ts.isImportEqualsDeclaration(node) &&
    ts.isExternalModuleReference(node.moduleReference)
  ) {
    add(node.moduleReference.expression);
  } else if (
    ts.isCallExpression(node) &&
    node.expression.kind === ts.SyntaxKind.ImportKeyword &&
    node.arguments.length === 1
  ) {
    add(node.arguments[0]);
  }
  ts.forEachChild(node, visit);
};

visit(sourceFile);
process.stdout.write(`${JSON.stringify({specifiers})}\n`);
