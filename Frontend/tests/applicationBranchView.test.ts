import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  branchIterationScope,
  isViewingHistoricalBranch,
  mergeBranches,
  resolveBranchChain,
  resolveCurrentBranchName
} from '../src/renderer/src/service/applicationBranches'

/**
 * 工作台内容区的只读口径：只有回看**非当前分支**才是历史分支。
 *
 * 分支模型下当前分支就是可编辑的那条，其余一律只读 —— 不再需要看版本状态。
 */
test('当前分支不算历史分支', () => {
  assert.equal(isViewingHistoricalBranch('dev', 'dev'), false)
})

test('切到非当前分支才算历史分支', () => {
  assert.equal(isViewingHistoricalBranch('dev', 'feature-x'), true)
})

test('未指定查看分支时按当前分支处理', () => {
  // viewingBranchName 为空表示跟随当前分支。
  assert.equal(isViewingHistoricalBranch('dev', undefined), false)
  assert.equal(isViewingHistoricalBranch('dev', ''), false)
})

/**
 * 分支表以磁盘为准。
 *
 * 回归：从首页打开一个多分支的应用时，磁盘上的分支曾被内存里刚初始化的单条分支
 * 无条件冲掉——分支选择器只剩一条，下拉列表里看不到其它分支。
 * 回退到内存只应发生在磁盘确实没有分支表时。
 */
test('磁盘有分支表时以磁盘为准，不被内存占位冲掉', () => {
  const disk = [
    { name: 'dev', createdAt: 1 },
    { name: 'feature-a', createdAt: 2 },
    { name: 'feature-b', createdAt: 3 }
  ]
  const memoryPlaceholder = [{ name: 'dev', createdAt: 0 }]

  const resolved = resolveBranchChain({
    diskBranches: disk as never,
    diskBranchName: 'feature-b',
    memoryBranches: memoryPlaceholder as never,
    memoryBranchName: 'dev'
  })

  assert.equal(resolved.branches?.length, 3, '三条分支都要保留')
  assert.equal(resolved.branchName, 'feature-b', '当前分支指针取磁盘的')
})

test('磁盘没有分支表时才回退到内存那份', () => {
  const memory = [{ name: 'dev', createdAt: 0 }]

  // application.json 尚无 branches 字段：保留前端初始化的分支表。
  assert.deepEqual(
    resolveBranchChain({
      diskBranches: undefined,
      diskBranchName: undefined,
      memoryBranches: memory as never,
      memoryBranchName: 'dev'
    }),
    { branches: memory, branchName: 'dev' }
  )
  // 空数组同样视为"没有分支表"。
  assert.deepEqual(
    resolveBranchChain({
      diskBranches: [],
      diskBranchName: '',
      memoryBranches: memory as never,
      memoryBranchName: 'dev'
    }),
    { branches: memory, branchName: 'dev' }
  )
})

test('磁盘有分支表但指针缺失时，指针退回内存那份', () => {
  const disk = [{ name: 'dev', createdAt: 1 }]

  // 有分支表却没有 branchName：不能留下"有表无指针"的空档，
  // 否则 currentBranch() 返回 undefined，分支入口整个不渲染。
  assert.equal(
    resolveBranchChain({
      diskBranches: disk as never,
      diskBranchName: undefined,
      memoryBranches: undefined,
      memoryBranchName: 'dev'
    }).branchName,
    'dev'
  )
})

/**
 * 写回前与磁盘合并分支表，不允许内存状态减少分支。
 *
 * 回归：新分支已创建并写盘，随后一次写回把 application.json 退回成只有旧分支，
 * 界面顶部只剩一条、下拉里没有新分支。内存状态可能陈旧于磁盘 —— 直接写内存那份
 * 会静默抹掉磁盘上的分支。
 */
test('写回时磁盘有、内存没有的分支要补回来', () => {
  const memory = [{ name: 'dev', createdAt: 1 }]
  const disk = [
    { name: 'dev', createdAt: 1 },
    { name: 'feature-x', createdAt: 2 }
  ]

  const merged = mergeBranches({
    memoryBranches: memory as never,
    diskBranches: disk as never,
    memoryBranchName: 'dev',
    diskBranchName: 'feature-x'
  })

  assert.deepEqual(
    merged.branches.map((branch) => branch.name),
    ['dev', 'feature-x'],
    '磁盘上的 feature-x 不能被内存的陈旧状态抹掉'
  )
})

test('两边都有的分支以内存为准（提交推送写了新的提交事实）', () => {
  const disk = [{ name: 'dev', createdAt: 1 }]
  const memory = [
    { name: 'dev', createdAt: 1, gitRef: { commitSha: 'abc1234', committedAt: 9 } }
  ]

  const merged = mergeBranches({
    memoryBranches: memory as never,
    diskBranches: disk as never,
    memoryBranchName: 'dev',
    diskBranchName: 'dev'
  })

  assert.equal(
    (merged.branches[0] as { gitRef?: { commitSha: string } }).gitRef?.commitSha,
    'abc1234',
    '本次操作写入的提交事实要保留'
  )
  assert.equal(merged.branches.length, 1, '不重复')
})

test('内存新建的分支要保留并追加在末尾', () => {
  const disk = [{ name: 'dev', createdAt: 1 }]
  const memory = [
    { name: 'dev', createdAt: 1 },
    { name: 'feature-x', createdAt: 2 }
  ]

  const merged = mergeBranches({
    memoryBranches: memory as never,
    diskBranches: disk as never,
    memoryBranchName: 'feature-x',
    diskBranchName: 'dev'
  })

  assert.deepEqual(
    merged.branches.map((branch) => branch.name),
    ['dev', 'feature-x']
  )
  assert.equal(merged.branchName, 'feature-x', '指针跟随本次新建的分支')
})

test('指针必须指向表里真实存在的分支', () => {
  const disk = [{ name: 'dev', createdAt: 1 }]

  // 指针指向一个不存在的分支时退回表尾，否则 currentBranch() 返回 undefined、
  // 分支入口整个不渲染。
  const merged = mergeBranches({
    memoryBranches: [] as never,
    diskBranches: disk as never,
    memoryBranchName: 'ghost',
    diskBranchName: ''
  })
  assert.equal(merged.branchName, 'dev')

  // 磁盘为空、内存也为空：不产生指针。
  const empty = mergeBranches({
    memoryBranches: [] as never,
    diskBranches: [] as never,
    memoryBranchName: '',
    diskBranchName: ''
  })
  assert.deepEqual(empty.branches, [])
  assert.equal(empty.branchName, undefined)
})

/**
 * 当前分支指针必须指向正在编辑的那条分支。
 *
 * 回归：新分支已创建并写盘，模板就绪时一份**发起迭代前**的应用快照被写回 ——
 * branches 数组还在（合并保住了），但指针被拉回旧分支，顶部显示的分支名与实际
 * 在编辑的分支不一致。
 */
test('陈旧快照不能把当前分支指针拉回旧分支', () => {
  const branches = [
    { name: 'dev', createdAt: 1 },
    { name: 'feature-x', createdAt: 2 }
  ]

  // 陈旧快照把指针留在 dev：不能接受，因为它不在本次操作的结果里。
  assert.equal(
    resolveCurrentBranchName(branches as never, 'feature-x'),
    'feature-x',
    '本次操作指向的分支要保留'
  )
  // 指针无效时退回表尾。
  assert.equal(resolveCurrentBranchName(branches as never, 'ghost'), 'feature-x')
  // 未传指针时取最后创建的分支。
  assert.equal(resolveCurrentBranchName(branches as never, undefined), 'feature-x')
  // 空表但传入了分支名：必须保留（见下面那条回归）。
  assert.equal(resolveCurrentBranchName([], 'dev'), 'dev')
  // 空表且没有传入分支名：不产生指针。
  assert.equal(resolveCurrentBranchName([], undefined), undefined)
})

/**
 * 回归：新建应用时分支表还空着，branchName 不能被写丢。
 *
 * 新建应用时表单只写了 `branchName`，首条分支记录要等进入工作台才建（它需要 lifecycle，
 * 那时才拿得到）。写盘前走的 mergeBranches 曾经因为"空表 ⇒ 指针不存在"把 branchName
 * 抹掉，落盘的 application.json 只剩 `branches: []` —— 于是模板基线提交后远端分支创建
 * 被整段跳过，界面弹出"当前应用未配置分支名，跳过远端分支创建"。
 */
test('新建应用时分支表为空，branchName 必须保留', () => {
  const merged = mergeBranches({
    memoryBranches: undefined,
    diskBranches: undefined,
    memoryBranchName: 'dev',
    diskBranchName: 'dev'
  })

  assert.deepEqual(merged.branches, [])
  assert.equal(merged.branchName, 'dev', '空表时指针是分支名的唯一来源，不能丢')
})

test('写回合并时指针也走不变式', () => {
  const memory = [
    { name: 'dev', createdAt: 1 },
    { name: 'feature-x', createdAt: 2 }
  ]
  const disk = [
    { name: 'dev', createdAt: 1 },
    { name: 'feature-x', createdAt: 2 }
  ]

  // 内存指针无效（ghost），合并后必须纠正到真实存在的分支。
  const merged = mergeBranches({
    memoryBranches: memory as never,
    diskBranches: disk as never,
    memoryBranchName: 'ghost',
    diskBranchName: 'dev'
  })
  assert.equal(merged.branchName, 'feature-x')
  assert.deepEqual(
    merged.branches.map((branch) => branch.name),
    ['dev', 'feature-x']
  )
})

/**
 * 回归：发起新迭代后界面必须回到设计阶段。
 *
 * 同一条分支上继续迭代时分支名不变，阶段 Provider 的 React key 如果只用分支名就
 * 不会重挂载，它会一直沿用内存里上一轮的手动阶段覆盖（例如"验收"），于是用户点了
 * 「发起新迭代」却发现还停在验收阶段。迭代令牌（lifecycle threadId，每次发起迭代
 * 都是新的 randomUUID）必须进 key。
 */
test('同一分支的新一轮迭代要产生不同的作用域键', () => {
  const firstRound = branchIterationScope('dev', {
    initialization: { threadId: 'thread-round-1' }
  } as never)
  const secondRound = branchIterationScope('dev', {
    initialization: { threadId: 'thread-round-2' }
  } as never)

  assert.notEqual(firstRound, secondRound, '同一分支的不同轮必须区分开，否则 Provider 不重挂载')
  assert.equal(firstRound, 'dev:thread-round-1')
  // 同一轮内重复计算要稳定，否则 Provider 会被无谓地重挂载。
  assert.equal(
    branchIterationScope('dev', { initialization: { threadId: 'thread-round-1' } } as never),
    firstRound
  )
  // 换分支同样产生新作用域。
  assert.notEqual(branchIterationScope('feature-x', { initialization: { threadId: 'thread-round-1' } } as never), firstRound)
  // 没有迭代令牌时退回分支名，保持可用。
  assert.equal(branchIterationScope('dev', undefined), 'dev')
})

/**
 * 回归：发起新迭代（新建分支）后，模板就绪时指针不能退回旧分支。
 *
 * 线上（2026092301）：dev 走完设计+计划并提交，随后发起新迭代新建 dev_1 并进入设计阶段；
 * 模板就绪卡片出现时，顶部切换从 dev_1 突然变回 dev，下拉里 dev 还被标成"当前分支"。
 *
 * 成因：`useApplicationTemplateGeneration` 在模板就绪时把 `planning.application` 写回，
 * 而那份快照是**发起迭代时**登记进规划状态的（早于 dev_1 创建），带的是 branchName=dev。
 * `saveApplication` 当时用"合并、内存优先"的口径，而 dev 在合并结果里真实存在，
 * 于是快照的指针赢了。
 *
 * 所以 `saveApplication` 必须走 disk-authoritative 的 `resolveBranchChain`。
 */
test('规划快照写回时，磁盘上的当前分支不能被拉回旧分支', () => {
  const diskBranches = [
    { name: 'dev', createdAt: 1 },
    { name: 'dev_1', createdAt: 2 }
  ]
  const snapshotBranches = [{ name: 'dev', createdAt: 1 }]

  const chain = resolveBranchChain({
    diskBranches: diskBranches as never,
    diskBranchName: 'dev_1',
    memoryBranches: snapshotBranches as never,
    memoryBranchName: 'dev'
  })

  assert.equal(chain.branchName, 'dev_1', '当前分支必须保持 dev_1，不能被快照拉回 dev')
  assert.deepEqual(
    chain.branches?.map((branch) => branch.name),
    ['dev', 'dev_1'],
    '两条分支都要在'
  )
})

test('磁盘还没有分支表时才用入参那份（新建应用）', () => {
  const chain = resolveBranchChain({
    diskBranches: [],
    diskBranchName: undefined,
    memoryBranches: undefined,
    memoryBranchName: 'dev'
  })
  assert.equal(chain.branchName, 'dev', '新建应用时分支名来自表单，磁盘还没有分支表')
})
