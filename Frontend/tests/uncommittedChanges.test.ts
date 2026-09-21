import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  hasConfirmedDesignDocument,
  shouldShowIncompleteChangesHint
} from '../src/renderer/src/components/AiChatPanel/utils'
import {
  isCheckpointCandidate,
  isModuleCheckpointCandidate
} from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/buildTaskStatus'
import {
  moduleOwnedFilesFromPlan,
  orphanUncommittedPaths,
  summarizePaths
} from '../src/renderer/src/hooks/useModuleOwnedFiles'
import {
  actionableCandidates,
  moduleCandidatesFromPlan
} from '../src/renderer/src/hooks/useModuleCandidates'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import VersionActions from '../src/renderer/src/components/VersionActions'
import { UncommittedChangesContext } from '../src/renderer/src/context/uncommittedChangesState'
import {
  shouldRenderCommitReminder,
  resolveCommitScope
} from '../src/renderer/src/components/AiChatPanel/components/MilestoneCommitReminder/visibility'
import { resolveTemplateCommitRow } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/templateCommitRow'
import { shouldInjectPlanningPlaceholder } from '../src/renderer/src/components/AiChatPanel/utils'
import {
  areAllSelected,
  isSelectionScopeValid,
  toggleSelectAll
} from '../src/renderer/src/components/AiChatPanel/components/MilestoneCommitReminder/fileSelection'
import {
  decideAcceptanceCommit,
  hasSensitivePath,
  ACCEPTANCE_COMMIT_MESSAGE
} from '../src/renderer/src/components/AiChatPanel/components/AcceptanceCommitDock/acceptanceCommit'

test('设计版本弱提醒只在文档确认之后出现', () => {
  // 需求还在澄清/生成时不能提示"已确认"。
  for (const stage of [
    'collecting_requirement',
    'analyzing_requirement',
    'awaiting_requirement_clarification',
    'generating_requirement_document',
    'awaiting_requirement_document_confirmation'
  ]) {
    assert.equal(hasConfirmedDesignDocument(stage), false, `${stage} 尚未确认，不应提示`)
  }
  // 需求文档确认之后的全部设计/计划阶段都应提示。
  for (const stage of [
    'generating_ui_designs',
    'awaiting_ui_design_confirmation',
    'awaiting_planning_stage_entry',
    'generating_technical_plan',
    'awaiting_technical_plan_confirmation'
  ]) {
    assert.equal(hasConfirmedDesignDocument(stage), true, `${stage} 已确认，应提示`)
  }
  // 模板就绪后由模板就绪卡承载提交入口，两边都提示会重复。
  assert.equal(hasConfirmedDesignDocument('ready_for_workbench'), false)
  assert.equal(hasConfirmedDesignDocument(undefined), false)
})

test('任务级检查点只标真正写过文件的任务', () => {
  const task = (over: Record<string, unknown> = {}): never =>
    ({ id: 't1', status: 'completed', ...over }) as never

  assert.equal(isCheckpointCandidate(task({ targetFiles: ['a.ts'] })), true)
  assert.equal(isCheckpointCandidate(task({ target_files: ['a.ts'] })), true)
  // 声明了目标文件但没完成：还在跑或失败，不能标检查点。
  assert.equal(isCheckpointCandidate(task({ status: 'running', targetFiles: ['a.ts'] })), false)
  assert.equal(isCheckpointCandidate(task({ status: 'failed', targetFiles: ['a.ts'] })), false)
  // already_satisfied 展示为完成，但确认无需改动、工作区没有新变更。
  assert.equal(
    isCheckpointCandidate(task({ status: 'already_satisfied', targetFiles: ['a.ts'] })),
    false
  )
  // 没有写入范围的任务不构成检查点。
  assert.equal(isCheckpointCandidate(task({ targetFiles: [] })), false)
  assert.equal(isCheckpointCandidate(task()), false)
})

test('未提交角标：有变更才显示，计数取可提交文件数', () => {
  const store = (count: number): never =>
    ({
      snapshot: undefined,
      count,
      refresh: async () => undefined,
      apply: () => undefined
    }) as never
  const application: never = {
    id: 'app-1',
    appName: '欢迎页',
    versions: [
      {
        id: 'app-1-v1-0',
        versionLabel: 'v1.0',
        major: 1,
        minor: 0,
        status: 'iterating',
        createdAt: 0,
        lifecycle: {} as never
      }
    ],
    currentVersionId: 'app-1-v1-0'
  } as never
  const props: never = {
    application,
    onPublish: () => undefined,
    onRollback: () => undefined,
    onStartIteration: () => undefined,
    onVersionSelect: () => undefined
  }

  const withChanges = renderToStaticMarkup(
    createElement(
      UncommittedChangesContext.Provider,
      { value: store(3) },
      createElement(VersionActions, props)
    )
  )
  assert.ok(withChanges.includes('workbench-version-uncommitted'), '有未提交变更时应显示角标')
  assert.ok(withChanges.includes('>3<'), '角标应显示可提交文件数')
  assert.ok(withChanges.includes('3 个文件未提交'), 'title 应说明未提交数量')

  const clean = renderToStaticMarkup(
    createElement(
      UncommittedChangesContext.Provider,
      { value: store(0) },
      createElement(VersionActions, props)
    )
  )
  assert.ok(!clean.includes('workbench-version-uncommitted'), '无未提交变更时不应显示角标')
})

test('弱提醒在读不到 Git 状态时静默，不渲染成告警', () => {
  const decide = (over: Partial<Parameters<typeof shouldRenderCommitReminder>[0]> = {}): boolean =>
    shouldRenderCommitReminder({
      hasResult: false,
      dismissed: false,
      hideWhenUnavailable: false,
      inspectError: '',
      inspecting: false,
      hasSnapshot: true,
      eligibleCount: 3,
      ...over
    })

  // 弱提醒（设计文档已确认）：设计阶段仓库可能尚未建立，读不到就什么都不显示。
  assert.equal(
    decide({ hideWhenUnavailable: true, inspectError: '当前工作目录还不是 Git 仓库。' }),
    false,
    '弱提醒读不到 Git 状态时应静默，而不是渲染"暂时无法准备提交"'
  )
  // 强提醒（模板初始化/验收通过）在"本该有仓库"的时点出现，读取失败值得报出来。
  assert.equal(
    decide({ hideWhenUnavailable: false, inspectError: '当前工作目录还不是 Git 仓库。' }),
    true,
    '强提醒读取失败仍应渲染，让用户看到原因'
  )

  // 已有变更时弱提醒正常渲染（迭代场景下仓库已存在）。
  assert.equal(decide({ hideWhenUnavailable: true, eligibleCount: 2 }), true)
  // 已提交/已暂缓/无变更：都不渲染。
  assert.equal(decide({ hasResult: true }), false)
  assert.equal(decide({ dismissed: true }), false)
  assert.equal(decide({ eligibleCount: 0 }), false)
  // 首帧尚未读到快照时不据此判定，避免闪一下。
  assert.equal(decide({ hasSnapshot: false, eligibleCount: 0 }), true)
})

test('模块级检查点只在整轮做完、且真的写过文件时提示', () => {
  const task = (status: string, targetFiles?: string[]): never =>
    ({ id: 't1', status, ...(targetFiles ? { targetFiles } : {}) }) as never
  const decide = (
    executionStatus: 'running' | 'completed' | 'failed' | 'requires_user_input',
    tasks: unknown[]
  ): boolean =>
    isModuleCheckpointCandidate({
      executionStatus,
      tasks: tasks as never
    })

  // 整体仍在跑、本轮任务全部完成且写过文件：正是"这个模块做完了、整体还没完"。
  assert.equal(decide('running', [task('completed', ['a.ts']), task('completed', ['b.ts'])]), true)
  // 还有任务没完成：模块没做完，不该提示。
  assert.equal(decide('running', [task('completed', ['a.ts']), task('running', ['b.ts'])]), false)
  // 轮次自身已结束：收尾语义交给整体结果卡，再提示是重复。
  assert.equal(decide('completed', [task('completed', ['a.ts'])]), false)
  assert.equal(decide('failed', [task('completed', ['a.ts'])]), false)
  // 空轮次没有"完成"可言。
  assert.equal(decide('running', []), false)
  // 整批都是 already_satisfied：展示为完成但工作区没有新变更，"可创建提交"是空头支票。
  assert.equal(decide('running', [task('already_satisfied', ['a.ts'])]), false)
  // 混着来：只要有一个任务真写过文件，本轮就有可提交的改动。
  assert.equal(
    decide('running', [task('already_satisfied', ['a.ts']), task('completed', ['b.ts'])]),
    true
  )
})

test('失败但留下代码时只引导审阅，且不与版本提醒重复', () => {
  const decide = (
    over: Partial<Parameters<typeof shouldShowIncompleteChangesHint>[0]> = {}
  ): boolean =>
    shouldShowIncompleteChangesHint({
      failed: false,
      hasCodeChanges: false,
      coveredByVersionReminder: false,
      ...over
    })

  // 失败且留下了变更：提示审阅、继续修复或撤销。
  assert.equal(decide({ failed: true, hasCodeChanges: true }), true)
  // 没失败不提示（成功路径由强提醒承载）。
  assert.equal(decide({ failed: false, hasCodeChanges: true }), false)
  // 失败但没有代码变更：没什么可审阅的。
  assert.equal(decide({ failed: true, hasCodeChanges: false }), false)
  // 快速修改失败时消息下方已有 VersionCommitReminder 的失败态，同一件事不说两遍。
  assert.equal(
    decide({ failed: true, hasCodeChanges: true, coveredByVersionReminder: true }),
    false
  )
})

test('遗漏变更：模块归属取自构建计划，读不到时不做判断', () => {
  // 两种字段拼写都要认（服务端 plan 用 target_files，工作流载荷用 targetFiles）。
  const plan = JSON.stringify({
    task_registry: {
      'page:a::page': { target_files: ['frontend/src/pages/A/index.tsx'] },
      'api:b': { targetFiles: ['./frontend/src/api/b.ts'] }
    }
  })
  const owned = moduleOwnedFilesFromPlan(plan)
  assert.equal(owned.size, 2)
  assert.ok(owned.has('frontend/src/pages/A/index.tsx'))
  // 反斜杠与开头的 ./ 都要归一化，否则路径对不上会整片误报成"未关联"。
  assert.ok(owned.has('frontend/src/api/b.ts'))

  // 计划缺失/损坏/结构不对：返回空集合，调用方据此不做遗漏判断。
  assert.equal(moduleOwnedFilesFromPlan('').size, 0)
  assert.equal(moduleOwnedFilesFromPlan('{ 不是 JSON').size, 0)
  assert.equal(moduleOwnedFilesFromPlan('{"task_registry": null}').size, 0)

  const paths = [
    'frontend/src/pages/A/index.tsx',
    'frontend/src/loose/scratch.ts',
    './frontend/src/api/b.ts'
  ]
  // 模块集合为空 = "还不知道哪些文件属于模块"，不是"全部都没归属"。
  assert.deepEqual(
    orphanUncommittedPaths({ uncommittedPaths: paths, moduleOwnedFiles: new Set() }),
    []
  )
  // 认领过的文件不算遗漏，其余才算；路径写法差异不影响判定。
  assert.deepEqual(orphanUncommittedPaths({ uncommittedPaths: paths, moduleOwnedFiles: owned }), [
    'frontend/src/loose/scratch.ts'
  ])

  // 副标题只列前几个再收尾，避免把提醒卡撑成一大块。
  assert.equal(summarizePaths(['a.ts', 'b.ts']), 'a.ts、b.ts')
  assert.equal(summarizePaths(['a.ts', 'b.ts', 'c.ts', 'd.ts']), 'a.ts、b.ts、c.ts 等 4 个文件')
})

test('中等提示：门禁通过等待验收 / 模块完成 / 失败留码 / 遗漏变更', () => {
  // M2 模块级候选提交点：本轮任务全完成、但整体仍在运行。
  // 任务必须声明过 targetFiles —— "完成"要真的写过文件才算数（见 isCheckpointCandidate）。
  const moduleCandidate = (over: Record<string, unknown> = {}): boolean =>
    isModuleCheckpointCandidate({
      executionStatus: 'running',
      tasks: [{ status: 'completed', targetFiles: ['a.ts'] }] as never,
      ...over
    } as never)
  assert.equal(moduleCandidate(), true, '模块做完而整体未结束时应记候选点')
  // 整体已结束/失败：不提示（结果卡承载收尾语义）。
  assert.equal(moduleCandidate({ executionStatus: 'completed' }), false)
  assert.equal(moduleCandidate({ executionStatus: 'failed' }), false)
  // 还有任务没完成：不能说"本轮模块已完成"。
  assert.equal(
    moduleCandidate({
      tasks: [
        { status: 'completed', targetFiles: ['a.ts'] },
        { status: 'running', targetFiles: ['b.ts'] }
      ] as never
    }),
    false
  )
  assert.equal(moduleCandidate({ tasks: [] }), false)
  // 整批 already_satisfied：展示为完成，但工作区没有新变更，"可创建提交"是空头支票。
  assert.equal(
    moduleCandidate({ tasks: [{ status: 'already_satisfied', targetFiles: ['a.ts'] }] as never }),
    false
  )
  // 完成了但没声明过写入范围：同样没有可提交的东西。
  assert.equal(moduleCandidate({ tasks: [{ status: 'completed' }] as never }), false)

  // M4 失败留码：主操作是审阅，不包装成可提交版本；与快速修改提醒去重。
  const incomplete = (over: Record<string, unknown> = {}): boolean =>
    shouldShowIncompleteChangesHint({
      failed: true,
      hasCodeChanges: true,
      coveredByVersionReminder: false,
      ...over
    } as never)
  assert.equal(incomplete(), true)
  assert.equal(incomplete({ failed: false }), false, '成功不提示')
  assert.equal(incomplete({ hasCodeChanges: false }), false, '没有代码变更不提示')
  assert.equal(
    incomplete({ coveredByVersionReminder: true }),
    false,
    '快速修改失败时已有 VersionCommitReminder 说同一件事，不重复'
  )

  // M5 遗漏变更：只有"没被任何模块认领"的才算遗漏。
  const owned = new Set(['frontend/src/pages/A/index.tsx'])
  assert.deepEqual(
    orphanUncommittedPaths({
      uncommittedPaths: ['frontend/src/pages/A/index.tsx', 'frontend/vite.config.ts'],
      moduleOwnedFiles: owned
    }),
    ['frontend/vite.config.ts']
  )
  // 路径写法不一致时要归一化，否则整片对不上、把所有文件误报成遗漏。
  assert.deepEqual(
    orphanUncommittedPaths({
      uncommittedPaths: ['./frontend/src/pages/A/index.tsx', 'frontend\\src\\pages\\A\\index.tsx'],
      moduleOwnedFiles: owned
    }),
    [],
    '同一文件的不同写法必须归一化后再比对'
  )
  // 还不知道哪些文件属于模块时，不做遗漏判断（不是"全部都没归属"）。
  assert.deepEqual(
    orphanUncommittedPaths({
      uncommittedPaths: ['a.ts'],
      moduleOwnedFiles: new Set()
    }),
    []
  )

  // M5 摘要只列前几个，避免把提醒卡撑大。
  assert.equal(summarizePaths(['a.ts', 'b.ts']), 'a.ts、b.ts')
  assert.equal(summarizePaths(['a.ts', 'b.ts', 'c.ts', 'd.ts']), 'a.ts、b.ts、c.ts 等 4 个文件')
})

test('提交提醒按业务代码计数，产物不点亮角标', () => {
  // 平台产物（规划文档、状态快照）与业务代码混在一起时的典型快照。
  const snapshot = {
    eligiblePaths: [
      '.xcodeagent/application-lifecycle.json',
      '.xcodeagent/plans/product-plan.json',
      'frontend/src/pages/PageWelcome/index.tsx'
    ],
    codePaths: ['frontend/src/pages/PageWelcome/index.tsx']
  }

  // 代码提交类提醒：只认业务代码，产物不参与计数。
  assert.deepEqual(
    resolveCommitScope({ snapshot, includePlatformArtifacts: false }),
    ['frontend/src/pages/PageWelcome/index.tsx'],
    '产物不该让"用户改了代码"的提醒亮起来'
  )

  // 设计版本弱提醒：必须带上产物 —— 设计阶段唯一的变更就是 .xcodeagent，
  // 按业务代码算永远是 0，这条提醒会彻底消失（文档 §4.3 要求它存在）。
  assert.deepEqual(
    resolveCommitScope({ snapshot, includePlatformArtifacts: true }),
    snapshot.eligiblePaths,
    '设计版本提醒要看全部产物'
  )

  // 首帧尚未读到快照：返回空数组，调用方据此不渲染，而不是崩掉。
  assert.deepEqual(resolveCommitScope({ snapshot: undefined, includePlatformArtifacts: false }), [])
  assert.deepEqual(resolveCommitScope({ snapshot: undefined, includePlatformArtifacts: true }), [])

  // 迭代刚启动、只有产物被清理的场景：业务代码口径为空 → 角标不亮。
  const iterationStart = {
    eligiblePaths: ['.xcodeagent/plans/product-plan.json', '.xcodeagent/AGENTS.md'],
    codePaths: []
  }
  assert.deepEqual(
    resolveCommitScope({ snapshot: iterationStart, includePlatformArtifacts: false }),
    []
  )
  assert.equal(
    resolveCommitScope({ snapshot: iterationStart, includePlatformArtifacts: true }).length,
    2,
    '但设计版本提醒仍应看到这些产物'
  )
})

test('模板卡：无待提交代码时报告基线，而不是建议一个做不到的动作', () => {
  // bootstrap 建仓时就 commit 了模板（git_manager.initialize_baseline），
  // 所以这张卡出现时通常已无初始化代码可提交。夹具模拟真实工作区：
  // 唯一变更是 lifecycle 状态文件，它被排除在业务代码口径外。
  const snapshot = {
    head: 'c9b4fed6a10754fc7ddf76db631e5b2e338f3f24',
    eligiblePaths: ['.xcodeagent/application-lifecycle.json'],
    codePaths: [] as string[]
  }

  // 业务代码口径为空 —— 这正是"0 个文件可提交"的来源。
  assert.deepEqual(
    resolveCommitScope({ snapshot, includePlatformArtifacts: false }),
    [],
    '只有 lifecycle 变更时不该有可提交的业务代码'
  )
  // 而产物口径非空：设计版本弱提醒仍能看到这些变更。
  assert.equal(
    resolveCommitScope({ snapshot, includePlatformArtifacts: true }).length,
    1,
    '产物仍可被设计版本提醒提交'
  )
})

test('模板卡提交区：没有可提交代码时报告基线，不给做不到的按钮', () => {
  const decide = (over: Partial<Parameters<typeof resolveTemplateCommitRow>[0]> = {}): string =>
    resolveTemplateCommitRow({
      showCommitArea: true,
      inspectError: '',
      inspecting: false,
      hasSnapshot: true,
      eligibleCount: 0,
      ...over
    })

  // 真实场景：bootstrap 已自动提交模板，唯一变更的 lifecycle 被排除在业务代码口径外。
  assert.equal(decide(), 'baseline', '无业务代码变更时应报告代码已保存、无需操作')
  // 关键回归：不能显示"建议创建初始化提交"配一个灰按钮。
  assert.notEqual(decide(), 'reminder', '没有可提交文件时不该给提交入口')

  // 有业务代码变更（比如用户在外部 IDE 改过文件）才给提交入口。
  assert.equal(decide({ eligibleCount: 2 }), 'reminder')

  // 读不到 Git 状态：报告错误并可重试（模板已生成，仓库本该存在）。
  assert.equal(decide({ inspectError: '当前工作目录还不是 Git 仓库。' }), 'error')
  // 错误优先于其他分支。
  assert.equal(decide({ inspectError: '读取失败', eligibleCount: 3 }), 'error')

  // 用户点过"稍后"：提醒不再出现，但**基线状态仍要显示** —— 它是事实陈述不是提醒。
  assert.equal(decide({ showCommitArea: false }), 'baseline', '"稍后"不该让状态消失')
  // 但"稍后"要能压住提醒本身。
  assert.equal(decide({ showCommitArea: false, eligibleCount: 2 }), 'none')

  // 首帧尚未读到快照：不判定，避免闪一下。
  assert.equal(decide({ hasSnapshot: false }), 'none')
  // 正在读取：同样不判定。
  assert.equal(decide({ inspecting: true }), 'none')
})

test('提交弹窗：全选按钮是切换，覆盖全选与清空', () => {
  const all = ['a.ts', 'b.ts', 'c.ts']

  // 全选态判定：空列表不算全选（没有文件时"全选"没有意义）。
  assert.equal(areAllSelected({ allPaths: all, selectedPaths: all }), true)
  assert.equal(areAllSelected({ allPaths: all, selectedPaths: ['a.ts'] }), false)
  assert.equal(areAllSelected({ allPaths: [], selectedPaths: [] }), false)

  // 切换：未全选则全选，已全选则清空 —— 全选后再点毫无反应会让人以为按钮坏了。
  assert.deepEqual(toggleSelectAll({ allPaths: all, selectedPaths: [] }), all)
  assert.deepEqual(toggleSelectAll({ allPaths: all, selectedPaths: ['a.ts'] }), all)
  assert.deepEqual(toggleSelectAll({ allPaths: all, selectedPaths: all }), [])
  // 清空由同一个按钮承担，所以不再单独提供"反选"。
  assert.deepEqual(toggleSelectAll({ allPaths: all, selectedPaths: all }), [], '全选态下点击即清空')

  // 快照为空时不产生任何选择（首帧尚未读到快照）。
  assert.deepEqual(toggleSelectAll({ allPaths: [], selectedPaths: [] }), [])
  // 选择列表里有已不在快照中的文件时，以快照为准，不把它算作"已全选"。
  assert.equal(
    areAllSelected({ allPaths: all, selectedPaths: ['a.ts', 'b.ts', 'c.ts', 'gone.ts'] }),
    true
  )
  assert.equal(areAllSelected({ allPaths: all, selectedPaths: ['a.ts', 'gone.ts'] }), false)
})

test('提交弹窗：可勾选文件必须都在可提交范围内', () => {
  // 真实场景：工作区有业务代码 + .xcodeagent 产物。
  const allChanged = [
    'frontend/src/pages/PageWelcome/index.tsx',
    '.xcodeagent/application-lifecycle.json',
    '.xcodeagent/plans/product-plan.json'
  ]

  // 正确接线：可提交范围是**全部变更**，提醒口径（默认勾选）才是收窄的业务代码。
  assert.equal(
    isSelectionScopeValid({ allPaths: allChanged, requestedPaths: allChanged }),
    true,
    '弹窗列出的文件都在可提交范围内，点"全选"应当能提交'
  )

  // 回归：把 requestedPaths 直接别名成提醒口径（只有业务代码）时，"全选"必然被后端拒绝。
  const narrowedToBusinessCode = ['frontend/src/pages/PageWelcome/index.tsx']
  assert.equal(
    isSelectionScopeValid({ allPaths: allChanged, requestedPaths: narrowedToBusinessCode }),
    false,
    '范围收窄后弹窗里仍列着产物 —— 这正是"所选文件已不属于当前可提交变更"的成因'
  )

  // 边界：没有变更时 vacuously true（不会误报错误）。
  assert.equal(isSelectionScopeValid({ allPaths: [], requestedPaths: [] }), true)
})

test('验收自动保存：只在能安全提交时自动执行，否则降级为手动审阅', () => {
  const decide = (over: Partial<Parameters<typeof decideAcceptanceCommit>[0]> = {}): string =>
    decideAcceptanceCommit({
      hasSnapshot: true,
      inspectError: '',
      unborn: false,
      hasStagedChanges: false,
      hasSensitivePath: false,
      eligibleCount: 3,
      ...over
    })

  // 正常情况：有变更且工作区状态可安全提交 → 自动保存。
  assert.equal(decide(), 'commit')

  // 工作区干净：无事可做，也不该提示（不是降级）。
  assert.equal(decide({ eligibleCount: 0 }), 'clean')

  // 有暂存内容：替用户提交会把他精心挑选的暂存区混进来，降级让他自己审阅。
  assert.equal(decide({ hasStagedChanges: true }), 'fallback')
  // 含敏感文件：后端会硬拒绝，所以不走自动路径。
  assert.equal(decide({ hasSensitivePath: true }), 'fallback')
  // 读不到 Git 状态 / 仓库没有基线：交给手动提醒去报错，自动路径不猜。
  assert.equal(decide({ inspectError: '当前工作目录还不是 Git 仓库。' }), 'fallback')
  assert.equal(decide({ hasSnapshot: false }), 'fallback')
  assert.equal(decide({ unborn: true }), 'fallback')

  // 降级优先于"干净"：读不到状态时 eligibleCount 不可信，不能当成 clean 静默。
  assert.equal(
    decide({ inspectError: '读取失败', eligibleCount: 0 }),
    'fallback',
    '读不到状态时必须降级，不能因为计数为 0 就静默'
  )

  // 提交信息带语义，让历史里能看出这是验收节点。
  assert.match(ACCEPTANCE_COMMIT_MESSAGE, /验收/)
})

test('敏感文件识别：覆盖后端会拒绝的那些文件名', () => {
  // 与 workspace.py::SENSITIVE_FILE_NAMES 一致。
  for (const name of [
    '.env',
    '.env.local',
    '.env.production',
    '.npmrc',
    '.netrc',
    'id_rsa',
    'id_ed25519'
  ]) {
    assert.equal(hasSensitivePath([`frontend/${name}`]), true, `${name} 应被识别为敏感文件`)
  }
  // 嵌套路径与 Windows 分隔符同样要认出来。
  assert.equal(hasSensitivePath(['backend\\config\\.env']), true)
  // 正常业务代码不误报。
  assert.equal(hasSensitivePath(['frontend/src/pages/PageWelcome/index.tsx']), false)
  assert.equal(hasSensitivePath(['.xcodeagent/application-lifecycle.json']), false)
  // 形似但不是敏感文件的不能误伤（.env.example 是模板自带的示例文件）。
  assert.equal(hasSensitivePath(['backend/.env.example']), false, '.env.example 不是敏感文件')
  assert.equal(hasSensitivePath([]), false)
})

test('候选提交点：只认全部任务做完、且真写过文件的模块', () => {
  // 夹具照抄真实计划的结构（build_units + task_registry 双表关联）。
  const plan = (units: unknown, registry: unknown): string =>
    JSON.stringify({ build_units: units, task_registry: registry })

  // 两个模块：page_welcome 做完（有文件），page_home 还有一个任务在跑。
  const content = plan(
    {
      'page:page_welcome': { kind: 'page', page_id: 'page_welcome', task_ids: ['t1'] },
      'page:page_home': { kind: 'page', page_id: 'page_home', task_ids: ['t2', 't3'] }
    },
    {
      t1: {
        unit_id: 'page:page_welcome',
        status: 'completed',
        target_files: ['frontend/src/pages/PageWelcome/index.tsx']
      },
      t2: {
        unit_id: 'page:page_home',
        status: 'completed',
        target_files: ['frontend/src/pages/PageHome/index.tsx']
      },
      t3: {
        unit_id: 'page:page_home',
        status: 'running',
        target_files: ['frontend/src/pages/PageHome/style.css']
      }
    }
  )
  const candidates = moduleCandidatesFromPlan(content)
  assert.equal(candidates.length, 1, '只有一个模块全部任务完成')
  assert.equal(candidates[0].unitId, 'page:page_welcome')
  assert.equal(candidates[0].label, 'page_welcome', '展示名优先取 page_id')
  assert.deepEqual(candidates[0].files, ['frontend/src/pages/PageWelcome/index.tsx'])

  // already_satisfied 展示为完成但没有新变更 → 不构成候选点（与任务级检查点同口径）。
  const noWrite = plan(
    { 'page:a': { kind: 'page', page_id: 'a', task_ids: ['t1'] } },
    { t1: { status: 'already_satisfied', target_files: ['a.ts'] } }
  )
  assert.deepEqual(moduleCandidatesFromPlan(noWrite), [], '无需改动不算候选点')
  // 完成了但没声明写入范围 → 同样没有可提交的东西。
  const noFiles = plan(
    { 'page:a': { kind: 'page', page_id: 'a', task_ids: ['t1'] } },
    { t1: { status: 'completed' } }
  )
  assert.deepEqual(moduleCandidatesFromPlan(noFiles), [])

  // 任务表里查不到的任务：宁可不提示，也不把模块误报成已完成。
  const missingTask = plan(
    { 'page:a': { kind: 'page', page_id: 'a', task_ids: ['t1', 'gone'] } },
    { t1: { status: 'completed', target_files: ['a.ts'] } }
  )
  assert.deepEqual(moduleCandidatesFromPlan(missingTask), [], '查不到的任务不能当作已完成')

  // 没有任务的模块（如 application:root）不参与。
  const noTasks = plan(
    { 'application:root': { kind: 'application', status: 'not_prepared', task_ids: [] } },
    {}
  )
  assert.deepEqual(moduleCandidatesFromPlan(noTasks), [])

  // 多任务模块的文件要合并去重，并归一化路径写法。
  const merged = plan(
    { 'page:a': { kind: 'page', page_id: 'a', task_ids: ['t1', 't2'] } },
    {
      t1: { status: 'completed', target_files: ['frontend/src/a.ts'] },
      t2: { status: 'completed', target_files: ['./frontend/src/a.ts', 'frontend\\src\\b.ts'] }
    }
  )
  assert.deepEqual(
    moduleCandidatesFromPlan(merged)[0].files,
    ['frontend/src/a.ts', 'frontend/src/b.ts'],
    '同一文件的不同写法要去重'
  )

  // 计划读不到/损坏时返回空数组，调用方据此不显示角标。
  assert.deepEqual(moduleCandidatesFromPlan(''), [])
  assert.deepEqual(moduleCandidatesFromPlan('{ 不是 JSON'), [])
  assert.deepEqual(moduleCandidatesFromPlan('{"build_units": null}'), [])
})

test('候选提交点要过滤到"文件还没提交"，否则提交后角标不消失', () => {
  const candidates = [
    { unitId: 'page:a', kind: 'page', label: 'a', files: ['frontend/src/a.ts'] },
    {
      unitId: 'page:b',
      kind: 'page',
      label: 'b',
      files: ['frontend/src/b.ts', 'frontend/src/b.css']
    }
  ]

  // 两个模块的文件都还没提交 → 都是候选。
  assert.equal(
    actionableCandidates({
      candidates,
      uncommittedPaths: ['frontend/src/a.ts', 'frontend/src/b.ts', 'frontend/src/b.css']
    }).length,
    2
  )

  // a 已提交 → 只剩 b。关键回归：不过滤的话 a 会一直挂着角标，
  // 暗示"可以先提交"而实际无事可做（与"提交后角标要刷新"同一个道理）。
  const afterPartialCommit = actionableCandidates({
    candidates,
    uncommittedPaths: ['frontend/src/b.ts', 'frontend/src/b.css']
  })
  assert.equal(afterPartialCommit.length, 1)
  assert.equal(afterPartialCommit[0].unitId, 'page:b')

  // 全部提交完 → 没有候选，角标消失。
  assert.deepEqual(
    actionableCandidates({ candidates, uncommittedPaths: [] }),
    [],
    '工作区干净时不该还有候选提交点'
  )
  // 模块只要**有一个**文件还没提交就算候选（可能只提交了它的一部分）。
  assert.equal(
    actionableCandidates({ candidates, uncommittedPaths: ['frontend/src/b.css'] })[0].unitId,
    'page:b'
  )
  // 没有已完成模块时不报错。
  assert.deepEqual(actionableCandidates({ candidates: [], uncommittedPaths: ['x.ts'] }), [])
})

test('规划状态未就绪时不注入占位，避免永久挡住需求输入卡', () => {
  const decide = (
    over: Partial<Parameters<typeof shouldInjectPlanningPlaceholder>[0]> = {}
  ): boolean =>
    shouldInjectPlanningPlaceholder({
      messageCount: 0,
      stage: 'generating_requirement_document',
      hasWorkflow: false,
      ...over
    })

  // 回归：规划状态尚未就绪（stage 未知）时不能注入。
  // 这条占位注入后不会被清除（那一轮没有任何 workflow 会到达），会一直渲染成
  // "正在处理"的加载卡，并让消息列表非空 —— 而需求输入卡只在空态渲染。
  assert.equal(
    decide({ stage: undefined }),
    false,
    'stage 未知时没有正面证据说明正在处理，不应注入占位'
  )
  assert.equal(decide({ stage: '' }), false, '空串同样视为未知')

  // 新迭代等待用户输入需求：不注入，让输入卡显示。
  assert.equal(decide({ stage: 'collecting_requirement' }), false)
  // 但确实有 workflow 在跑时，说明正在处理，仍要注入。
  assert.equal(decide({ stage: 'collecting_requirement', hasWorkflow: true }), true)

  // 其它设计/计划阶段确实在处理：注入占位。
  assert.equal(decide({ stage: 'generating_ui_designs' }), true)
  assert.equal(decide({ stage: 'awaiting_technical_plan_confirmation' }), true)

  // 已有消息时不注入（占位只用于"空列表且正在处理"）。
  assert.equal(decide({ messageCount: 3 }), false)
})
