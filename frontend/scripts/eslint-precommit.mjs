#!/usr/bin/env node
// pre-commit always runs hooks with cwd = repo root, and passes repo-root-relative
// file paths (e.g. "frontend/src/App.tsx"). ESLint's flat-config `files` globs are
// matched relative to its own `cwd` option, so invoking the plain CLI from the repo
// root against those paths silently fails to match `src/components/ui/**` overrides
// in frontend/eslint.config.js. This wrapper re-bases both cwd and the file list to
// frontend/ before handing off to ESLint's Node API.
import { ESLint } from 'eslint'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const files = process.argv.slice(2).map((f) => path.relative(frontendDir, path.resolve(f)))

const eslint = new ESLint({ cwd: frontendDir, fix: true })
const results = await eslint.lintFiles(files)
await ESLint.outputFixes(results)

const formatter = await eslint.loadFormatter('stylish')
const resultText = formatter.format(results)
if (resultText) {
  console.log(resultText)
}

const errorCount = results.reduce((sum, r) => sum + r.errorCount, 0)
process.exit(errorCount > 0 ? 1 : 0)
