import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readDagGenerationSnapshot } from '../src/renderer/src/service/agUiAgent'

/** 构造最小 DAG 阶段事件，减少协议测试样板。 */
function stage(id: string, output: Record<string, unknown>): Record<string, unknown> {
  return {
    id,
    name: id,
    status: 'completed',
    detail: `完成 ${id}`,
    output
  }
}

test('阶段产物会按 7 种结构化类型解析并保留任务和产物兼容字段', () => {
  const snapshot = readDagGenerationSnapshot({
    stages: [
      stage('unit_skeleton', {
        kind: 'unit_graph',
        schemaVersion: 'build-unit-graph.v3',
        reused: false,
        units: [{ id: 'frontend:shell', kind: 'frontend', status: 'prepared', taskCount: 1 }],
        edges: { items: [{ from: 'application:root', to: 'frontend:shell', type: 'contains' }] },
        validation: { isValid: true, issues: [] }
      }),
      stage('build_context', {
        kind: 'build_context',
        target: { type: 'page', id: 'home' },
        requiredUnitIds: ['frontend:shell'],
        endpointIds: ['home.list'],
        apiContractIds: ['home-api'],
        dataSourceIds: ['main'],
        databaseStatus: 'completed',
        reusableTaskIds: []
      }),
      stage('contract_validation', {
        kind: 'contract_validation',
        isValid: true,
        checkedEndpointIds: ['home.list'],
        checkedApiContractIds: ['home-api'],
        issues: []
      }),
      stage('model_planning', {
        kind: 'candidate_tasks',
        tasks: [{ id: 'candidate', title: '候选任务', owner: 'frontend', status: 'pending' }],
        summary: { frontend: 1, backend: 0, database: 0 }
      }),
      stage('task_compilation', {
        kind: 'compiled_tasks',
        tasks: [{ id: 'compiled', title: '编译任务', owner: 'backend', status: 'pending' }],
        edges: { items: [], truncated: false },
        summary: { frontend: 0, backend: 1, database: 0 }
      }),
      stage('dag_validation', {
        kind: 'dag_validation',
        isValid: true,
        roots: ['compiled'],
        leaves: ['compiled'],
        topologicalOrder: ['compiled'],
        batches: [{ index: 1, mode: 'serial', taskIds: ['compiled'] }],
        issues: []
      }),
      stage('artifact_persistence', {
        kind: 'artifacts',
        artifacts: [{ id: 'dag', name: 'BUILD_TASK_DAG.md', kind: 'markdown' }],
        count: 1
      })
    ],
    tasks: [{ id: 'compiled', title: '编译任务', owner: 'backend', status: 'pending' }],
    summary: { unitCount: 1, taskCount: 1, batchCount: 1 },
    artifacts: [{ id: 'dag', name: 'BUILD_TASK_DAG.md', kind: 'markdown' }]
  })

  assert.ok(snapshot)
  assert.deepEqual(
    snapshot.stages.map((item) => item.output?.kind),
    [
      'unit_graph',
      'build_context',
      'contract_validation',
      'candidate_tasks',
      'compiled_tasks',
      'dag_validation',
      'artifacts'
    ]
  )
  assert.equal(snapshot.tasks[0]?.id, 'compiled')
  assert.equal(snapshot.artifacts[0]?.name, 'BUILD_TASK_DAG.md')
})

test('未知阶段产物和非法任务会被过滤，不影响其他阶段快照', () => {
  const snapshot = readDagGenerationSnapshot({
    stages: [
      stage('unit_skeleton', { kind: 'unknown_output', secret: 'discard' }),
      stage('artifact_persistence', {
        kind: 'compiled_tasks',
        tasks: [],
        edges: { items: [], truncated: false },
        summary: { frontend: 0, backend: 0, database: 0 }
      }),
      stage('task_compilation', {
        kind: 'compiled_tasks',
        tasks: [
          { id: '', title: '无效任务', status: 'pending' },
          { id: 'valid', title: '有效任务', status: 'pending' }
        ],
        edges: { items: [], truncated: true },
        summary: { frontend: 0, backend: 1, database: 0 }
      })
    ],
    summary: {}
  })

  assert.ok(snapshot)
  assert.equal(snapshot.stages[0]?.output, undefined)
  assert.equal(snapshot.stages[1]?.output, undefined)
  const output = snapshot.stages[2]?.output
  assert.equal(output?.kind, 'compiled_tasks')
  assert.equal(output?.kind === 'compiled_tasks' ? output.tasks.length : 0, 1)
})

test('依赖边保留服务端截断标记', () => {
  const snapshot = readDagGenerationSnapshot({
    stages: [
      stage('unit_skeleton', {
        kind: 'unit_graph',
        units: [],
        edges: { items: [{ from: 'a', to: 'b', type: 'depends_on' }], truncated: true },
        validation: { isValid: true, issues: [] }
      })
    ],
    summary: {}
  })

  assert.ok(snapshot)
  const output = snapshot.stages[0]?.output
  assert.equal(output?.kind === 'unit_graph' ? output.edges.truncated : false, true)
})
