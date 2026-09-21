import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  isViewingHistoricalVersion,
  mergeVersionChain,
  resolveCurrentVersionId,
  resolveVersionChain
} from '../src/renderer/src/service/applicationVersions'

/**
 * 工作台内容区的只读口径：只有回看**非活跃版本**才是历史版本。
 *
 * 这里刻意不看版本状态。当前版本发布后也是 released、阶段同样不可点，但它仍是
 * 用户正在推进的应用，必须保留对话区的执行情况视图——曾经用"阶段锁定"判断，
 * 结果刚发布的当前版本被换成了只读的应用文件/应用预览，用户看不到执行情况。
 */
test('活跃版本（含刚发布的 released）不算历史版本', () => {
  assert.equal(isViewingHistoricalVersion('app-v1-2', 'app-v1-2'), false)
})

test('切到非活跃版本才算历史版本', () => {
  assert.equal(isViewingHistoricalVersion('app-v1-2', 'app-v1-0'), true)
})

test('未指定查看版本时按活跃版本处理', () => {
  // viewingVersionId 为空表示跟随版本链头，即当前迭代。
  assert.equal(isViewingHistoricalVersion('app-v1-2', undefined), false)
  assert.equal(isViewingHistoricalVersion('app-v1-2', ''), false)
})

/**
 * 版本链以磁盘为准。
 *
 * 回归：从首页打开一个已发布多个版本的应用时，磁盘上的 v1.0/v1.1/v1.2 曾被内存里
 * 刚初始化的 `[v1.0]` 无条件冲掉——版本选择器只剩 v1.0，下拉列表里看不到其它版本。
 * 回退到内存只应发生在磁盘确实没有版本链时（旧数据兼容）。
 */
test('磁盘有版本链时以磁盘为准，不被内存占位冲掉', () => {
  const disk = [
    { id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' },
    { id: 'app-v1-1', versionLabel: 'v1.1', status: 'released' },
    { id: 'app-v1-2', versionLabel: 'v1.2', status: 'iterating' }
  ]
  const memoryPlaceholder = [{ id: 'app-v1-0', versionLabel: 'v1.0', status: 'iterating' }]

  const resolved = resolveVersionChain({
    diskVersions: disk as never,
    diskCurrentVersionId: 'app-v1-2',
    memoryVersions: memoryPlaceholder as never,
    memoryCurrentVersionId: 'app-v1-0'
  })

  assert.equal(resolved.versions?.length, 3, '三个版本都要保留')
  assert.equal(resolved.currentVersionId, 'app-v1-2', '当前版本指针取磁盘的')
})

test('磁盘没有版本链时才回退到内存那份', () => {
  const memory = [{ id: 'app-v1-0', versionLabel: 'v1.0', status: 'iterating' }]

  // application.json 尚无 versions 字段（旧数据）：保留前端初始化的版本链。
  assert.deepEqual(
    resolveVersionChain({
      diskVersions: undefined,
      diskCurrentVersionId: undefined,
      memoryVersions: memory as never,
      memoryCurrentVersionId: 'app-v1-0'
    }),
    { versions: memory, currentVersionId: 'app-v1-0' }
  )
  // 空数组同样视为"没有版本链"。
  assert.deepEqual(
    resolveVersionChain({
      diskVersions: [],
      diskCurrentVersionId: '',
      memoryVersions: memory as never,
      memoryCurrentVersionId: 'app-v1-0'
    }),
    { versions: memory, currentVersionId: 'app-v1-0' }
  )
})

test('磁盘有版本链但指针缺失时，指针退回内存那份', () => {
  const disk = [{ id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' }]

  // 有版本链却没有 currentVersionId：不能留下"有链无指针"的空档，
  // 否则 currentVersion() 返回 undefined，版本入口整个不渲染。
  assert.equal(
    resolveVersionChain({
      diskVersions: disk as never,
      diskCurrentVersionId: undefined,
      memoryVersions: undefined,
      memoryCurrentVersionId: 'app-v1-0'
    }).currentVersionId,
    'app-v1-0'
  )
})

/**
 * 写回前与磁盘合并版本链，不允许内存状态减少版本。
 *
 * 回归：v1.1 已创建并写盘，随后一次写回把 application.json 退回成只有 v1.0
 * （git 记录可见 v1.1 的提交还在），界面顶部只剩 v1.0、下拉里没有 v1.1。
 * 内存状态可能陈旧于磁盘 —— 直接写内存那份会静默抹掉磁盘上的版本。
 */
test('写回时磁盘有、内存没有的版本要补回来', () => {
  const memory = [{ id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' }]
  const disk = [
    { id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' },
    { id: 'app-v1-1', versionLabel: 'v1.1', status: 'iterating' }
  ]

  const merged = mergeVersionChain({
    memoryVersions: memory as never,
    diskVersions: disk as never,
    memoryCurrentVersionId: 'app-v1-0',
    diskCurrentVersionId: 'app-v1-1'
  })

  assert.deepEqual(
    merged.versions?.map((v) => v.id),
    ['app-v1-0', 'app-v1-1'],
    '磁盘上的 v1.1 不能被内存的陈旧状态抹掉'
  )
})

test('两边都有的版本以内存为准（发布把 v1.0 转成 released）', () => {
  const disk = [{ id: 'app-v1-0', versionLabel: 'v1.0', status: 'iterating' }]
  const memory = [{ id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' }]

  const merged = mergeVersionChain({
    memoryVersions: memory as never,
    diskVersions: disk as never,
    memoryCurrentVersionId: 'app-v1-0',
    diskCurrentVersionId: 'app-v1-0'
  })

  assert.equal(merged.versions?.[0]?.status, 'released', '本次操作更新的状态要保留')
  assert.equal(merged.versions?.length, 1, '不重复')
})

test('内存新建的版本要保留并追加在末尾', () => {
  const disk = [{ id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' }]
  const memory = [
    { id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' },
    { id: 'app-v1-1', versionLabel: 'v1.1', status: 'iterating' }
  ]

  const merged = mergeVersionChain({
    memoryVersions: memory as never,
    diskVersions: disk as never,
    memoryCurrentVersionId: 'app-v1-1',
    diskCurrentVersionId: 'app-v1-0'
  })

  assert.deepEqual(
    merged.versions?.map((v) => v.id),
    ['app-v1-0', 'app-v1-1']
  )
  assert.equal(merged.currentVersionId, 'app-v1-1', '指针跟随本次新建的版本')
})

test('指针必须指向链上真实存在的版本', () => {
  const disk = [{ id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' }]

  // 指针指向一个不存在的版本时退回链尾，否则 currentVersion() 返回 undefined、
  // 版本入口整个不渲染。
  const merged = mergeVersionChain({
    memoryVersions: [] as never,
    diskVersions: disk as never,
    memoryCurrentVersionId: 'app-v1-9',
    diskCurrentVersionId: ''
  })
  assert.equal(merged.currentVersionId, 'app-v1-0')

  // 磁盘为空、内存也为空：不产生指针。
  const empty = mergeVersionChain({
    memoryVersions: [] as never,
    diskVersions: [] as never,
    memoryCurrentVersionId: '',
    diskCurrentVersionId: ''
  })
  assert.deepEqual(empty.versions, [])
  assert.equal(empty.currentVersionId, undefined)
})

/**
 * 当前版本指针必须指向正在编辑的那一轮迭代。
 *
 * 回归：v1.1 已创建并写盘，模板就绪时一份**发起迭代前**的应用快照被写回 ——
 * versions 数组还在（合并保住了），但指针被拉回 v1.0，顶部版本号从 v1.1 掉成 v1.0，
 * 而实际在编辑的是 v1.1。线上两个工作区（2105、2106）都是这个症状。
 */
test('有 iterating 版本时，指针必须指向它，不能被陈旧快照拉回已发布版本', () => {
  const versions = [
    { id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' },
    { id: 'app-v1-1', versionLabel: 'v1.1', status: 'iterating' }
  ]

  // 陈旧快照把指针留在 v1.0：必须纠正到 v1.1。
  assert.equal(
    resolveCurrentVersionId(versions as never, 'app-v1-0'),
    'app-v1-1',
    '已发布版本不是"当前迭代"，指针不能被它占住'
  )
  // 指针本来就对时保持不变。
  assert.equal(resolveCurrentVersionId(versions as never, 'app-v1-1'), 'app-v1-1')
  // 多个 iterating（异常数据）取最后一个。
  assert.equal(
    resolveCurrentVersionId(
      [...versions, { id: 'app-v1-2', versionLabel: 'v1.2', status: 'iterating' }] as never,
      'app-v1-0'
    ),
    'app-v1-2'
  )
})

test('没有 iterating 版本时保留传入的指针（刚发布、尚未发起迭代）', () => {
  const released = [{ id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' }]

  assert.equal(resolveCurrentVersionId(released as never, 'app-v1-0'), 'app-v1-0')
  // 指针无效时退回链尾，不能留下"有链无指针"的空档。
  assert.equal(resolveCurrentVersionId(released as never, 'app-v9-9'), 'app-v1-0')
  assert.equal(resolveCurrentVersionId(released as never, undefined), 'app-v1-0')
  // 空链不产生指针。
  assert.equal(resolveCurrentVersionId([], 'x'), undefined)
})

test('写回合并时指针也走不变式', () => {
  const memory = [
    { id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' },
    { id: 'app-v1-1', versionLabel: 'v1.1', status: 'iterating' }
  ]
  const disk = [
    { id: 'app-v1-0', versionLabel: 'v1.0', status: 'released' },
    { id: 'app-v1-1', versionLabel: 'v1.1', status: 'iterating' }
  ]

  // 内存指针陈旧（v1.0），合并后必须纠正到 v1.1。
  const merged = mergeVersionChain({
    memoryVersions: memory as never,
    diskVersions: disk as never,
    memoryCurrentVersionId: 'app-v1-0',
    diskCurrentVersionId: 'app-v1-0'
  })
  assert.equal(merged.currentVersionId, 'app-v1-1')
  assert.deepEqual(
    merged.versions?.map((v) => v.id),
    ['app-v1-0', 'app-v1-1']
  )
})
