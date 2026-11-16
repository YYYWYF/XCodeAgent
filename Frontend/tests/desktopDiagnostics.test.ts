import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { describeDiagnosticError } from '../src/main/diagnostics/errors'
import { DiagnosticStore } from '../src/main/diagnostics/store'

/** 在隔离目录验证本机真实的摘要和事件写入。 */
function withDiagnostics(run: (root: string) => void): void {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'desktop-diagnostics-'))
  try {
    run(root)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
}

/** 比对磁盘上的摘要，覆盖维护前后的独立读取。 */
function readSummary(directory: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(path.join(directory, 'summary.json'), 'utf8'))
}

test('persists startup, phases and an explicit exit code', () => {
  withDiagnostics((root) => {
    const run = new DiagnosticStore(root, '1.2.3', false)
    assert.equal(path.dirname(run.directory), path.join(root, 'source-dev'))
    assert.equal(readSummary(run.directory).status, 'running')
    run.stage('backend_ready')
    run.finish('update_exit', 0)
    assert.equal(readSummary(run.directory).version, '1.2.3')
    assert.equal(readSummary(run.directory).stage, 'backend_ready')
    assert.equal(readSummary(run.directory).status, 'update_exit')
    assert.equal(readSummary(run.directory).exitCode, 0)

    const lines = fs
      .readFileSync(path.join(run.directory, 'events.jsonl'), 'utf8')
      .trim()
      .split('\n')
    assert.deepEqual(
      lines.map((line) => JSON.parse(line).event),
      ['startup', 'stage', 'exit']
    )
  })
})

test('recovers a dead process while preserving active and completed runs', () => {
  withDiagnostics((root) => {
    const orphan = new DiagnosticStore(root, '1.2.3', false)
    orphan.stage('backend_starting')
    const deadPid = spawnSync(process.execPath, ['-e', '']).pid
    assert.ok(deadPid > 0)
    const orphanSummary = readSummary(orphan.directory)
    const summaryFile = path.join(orphan.directory, 'summary.json')
    fs.writeFileSync(summaryFile, JSON.stringify({ ...orphanSummary, pid: deadPid }))
    const active = new DiagnosticStore(root, '1.2.3', true)
    const completed = new DiagnosticStore(root, '1.2.3', true)
    completed.finish('second_instance', 0)

    active.maintain()
    assert.equal(readSummary(orphan.directory).status, 'interrupted')
    assert.equal(readSummary(orphan.directory).stage, 'backend_starting')
    assert.equal(readSummary(orphan.directory).exitCode, undefined)
    assert.equal(readSummary(active.directory).status, 'running')
    assert.equal(readSummary(completed.directory).status, 'second_instance')
  })
})

test('retains valid JSON lines and newest events after rotation', () => {
  withDiagnostics((root) => {
    const run = new DiagnosticStore(root, '1.2.3', true)
    for (let index = 0; index < 110; index++) {
      run.event('sample', { index, data: '状'.repeat(10000) }, false)
    }
    const events = fs.readFileSync(path.join(run.directory, 'events.jsonl'), 'utf8')
    assert.ok(Buffer.byteLength(events) <= 2 * 1024 * 1024)
    const records = events
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line))
    assert.ok(records[0].index > 0)
    assert.equal(records.at(-1)?.index, 109)
  })
})

test('prunes expired and oldest finished runs across packaged and source directories', () => {
  withDiagnostics((root) => {
    const now = Date.now()
    const deadPid = spawnSync(process.execPath, ['-e', '']).pid
    assert.ok(deadPid > 0)
    const expired = new DiagnosticStore(root, '1.2.3', true)
    const oldest = new DiagnosticStore(root, '1.2.3', false)
    const newest = new DiagnosticStore(root, '1.2.3', true)
    for (const [run, age] of [
      [expired, 15],
      [oldest, 2],
      [newest, 1]
    ] as const) {
      run.finish('normal_exit', 0)
      const time = new Date(now - age * 24 * 60 * 60 * 1000).toISOString()
      fs.writeFileSync(
        path.join(run.directory, 'summary.json'),
        JSON.stringify({
          ...readSummary(run.directory),
          pid: deadPid,
          startedAt: time,
          endedAt: time
        })
      )
    }
    for (const run of [oldest, newest]) {
      const dump = path.join(run.directory, 'test.dmp')
      fs.writeFileSync(dump, '')
      fs.truncateSync(dump, 110 * 1024 * 1024)
    }

    const current = new DiagnosticStore(root, '1.2.3', true)
    current.maintain(now)
    assert.equal(fs.existsSync(expired.directory), false)
    assert.equal(fs.existsSync(oldest.directory), false)
    assert.equal(fs.existsSync(newest.directory), true)
    assert.equal(fs.existsSync(current.directory), true)
  })
})

test('excludes untrusted exception text, paths and URLs', () => {
  const error = Object.assign(new TypeError('token=private-token'), { code: 'EACCES' })
  error.stack = [
    error.toString(),
    '    at open (C:\\Users\\private-user\\index.js:23:2)',
    '    at invoke (/Users/private-user/backendService.ts:8:16)',
    '    at page (https://private-host/secret.js:1:1)',
    '    at node:internal/process/task_queues:59:5'
  ].join('\n')
  assert.deepEqual(describeDiagnosticError(error), {
    errorType: 'TypeError',
    errorCode: 'EACCES',
    stack: 'index.js:23:2\nbackendService.ts:8:16\nnode:internal/process/task_queues:59:5'
  })
})
