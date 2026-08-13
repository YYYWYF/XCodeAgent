import assert from 'node:assert/strict'
import { readCodeAnalysisResult } from '../src/renderer/src/service/codeAnalysis'

const valid = readCodeAnalysisResult({
  schemaVersion: 1,
  runId: 'run-1',
  threadId: 'thread-1',
  status: 'in_progress',
  action: 'scan',
  progress: {
    stage: 'analyzing',
    message: '正在分析前端代码',
    percent: 55
  },
  activeToolActivity: {
    callId: 'call-1',
    tool: 'read_file',
    category: 'read',
    status: 'running',
    message: '正在读取文件：/Frontend/src/App.tsx',
    path: '/Frontend/src/App.tsx'
  }
})

assert.equal(valid?.progress?.stage, 'analyzing')
assert.equal(valid?.activeToolActivity?.path, '/Frontend/src/App.tsx')
assert.equal(readCodeAnalysisResult({ schemaVersion: 2 }), undefined)
assert.equal(
  readCodeAnalysisResult({
    schemaVersion: 1,
    runId: 'run-1',
    threadId: 'thread-1',
    status: 'cancelled',
    action: 'scan'
  }),
  undefined
)

console.log('Code analysis frontend tests passed.')
