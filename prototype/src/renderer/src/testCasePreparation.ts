export type TestCasePreparationStatus =
  | 'waiting-plan'
  | 'queued'
  | 'generating'
  | 'validating'
  | 'ready'
  | 'failed'
  | 'stale'

/** 业务测试用例后台生成任务的执行类型。 */
export type TestCaseGenerationTaskType = 'async' | 'tide'

/** 测试用例生成任务类型的统一展示文案，供选择弹框与后台任务抽屉共同使用。 */
export const TEST_CASE_GENERATION_TASK_TYPE_META: Record<
  TestCaseGenerationTaskType,
  { description: string; label: string }
> = {
  async: {
    label: '异步任务',
    description: '使用常规算力资源，在后台异步执行，会消耗您的码豆余额。'
  },
  tide: {
    label: '潮汐任务',
    description: '使用极算平台闲时算力资源，不消耗码豆，但任务优先级较低。'
  }
}

export type TestCaseGroupStatus = 'queued' | 'generating' | 'completed' | 'failed'

export type TestCaseEstimateGroup = {
  id: string
  label: string
  total: number
  coverage: string
}

/** 计划确认后用于文档预估、顶部进度和异步生成任务的统一用例分组基线。 */
export const TEST_CASE_ESTIMATE_GROUPS: TestCaseEstimateGroup[] = [
  { id: 'introduction', label: '回检介绍', total: 1, coverage: '页面访问、流程说明与进入回检入口' },
  { id: 'my-rechecks', label: '我的回检', total: 3, coverage: '列表加载、回检提交、必填校验与状态筛选' },
  { id: 'query-api', label: '回检查询接口', total: 2, coverage: '当前用户数据、分页参数与状态筛选' }
]

export type TestCaseBlueprint = {
  expected: string
  groupId: string
  id: string
  preconditions: string[]
  scenario: string
  steps: string[]
  testScript: string
  title: string
}

/** 当前演示应用的业务用例清单：页面、接口和用例 Workflow 共同消费这份稳定 ID。 */
export const TEST_CASE_BLUEPRINTS: TestCaseBlueprint[] = [
  {
    id: 'introduction-1',
    groupId: 'introduction',
    scenario: '回检介绍',
    title: '访问回检介绍并查看流程',
    preconditions: ['需求文档和项目计划已确认', '应用测试环境已启动'],
    steps: ['进入“回检介绍”页面', '查看回检流程说明', '点击入口进入“我的回检”页面'],
    testScript: "open('/recheck-introduction'); expectText('回检流程'); click('开始回检'); expectPath('/my-rechecks');",
    expected: '流程说明完整展示，入口可以正确跳转到“我的回检”。'
  },
  {
    id: 'my-rechecks-1',
    groupId: 'my-rechecks',
    scenario: '我的回检',
    title: '进入我的回检查看列表',
    preconditions: ['应用测试环境已启动', '存在可查询的回检单数据'],
    steps: ['进入“我的回检”页面', '等待列表加载完成', '查看回检单状态和处理结果'],
    testScript: "open('/my-rechecks'); waitFor('[data-testid=recheck-list]'); expectRows('> 0');",
    expected: '列表正常展示当前用户回检单及其状态。'
  },
  {
    id: 'my-rechecks-2',
    groupId: 'my-rechecks',
    scenario: '我的回检',
    title: '提交回检单并校验必填项',
    preconditions: ['已进入“我的回检”页面'],
    steps: ['点击提交回检入口', '不填写必填项直接提交', '补齐必填内容后再次提交'],
    testScript: "click('提交回检'); submit(); expectText('请填写'); fillRequired(); submit(); expectToast('提交成功');",
    expected: '缺少必填项时给出明确提示，补齐后可成功提交。'
  },
  {
    id: 'my-rechecks-3',
    groupId: 'my-rechecks',
    scenario: '我的回检',
    title: '按状态筛选回检单',
    preconditions: ['列表中存在不同审核状态的回检单'],
    steps: ['选择一个审核状态', '执行筛选', '核对列表结果'],
    testScript: "select('status', '待审核'); click('查询'); expectEachRow({ status: '待审核' });",
    expected: '列表仅保留对应状态的回检单。'
  },
  {
    id: 'query-api-1',
    groupId: 'query-api',
    scenario: '回检查询接口',
    title: '分页查询当前用户回检单',
    preconditions: ['查询接口可访问'],
    steps: ['调用 GET /api/rechecks/my', '传入 page 和 size 参数', '核对 list 和 total'],
    testScript: "GET('/api/rechecks/my?page=1&size=10'); expectStatus(200); expectSchema('data.list,data.total');",
    expected: '接口返回当前用户的分页数据，list 与 total 字段正确。'
  },
  {
    id: 'query-api-2',
    groupId: 'query-api',
    scenario: '回检查询接口',
    title: '按状态参数查询回检单',
    preconditions: ['查询接口可访问'],
    steps: ['调用 GET /api/rechecks/my', '传入 status 参数', '核对返回数据'],
    testScript: "GET('/api/rechecks/my?status=待审核'); expectStatus(200); expectEach('data.list', { status: '待审核' });",
    expected: '接口仅返回匹配状态的回检单。'
  }
]

export type TestCaseGroup = {
  id: string
  label: string
  generated: number
  total: number
  status: TestCaseGroupStatus
}

/** 顺序队列中单条用例的生成状态；与右侧目录树状态无关，只表达后台生成次序。 */
export type TestCasePrepCaseStatus = 'queued' | 'generating' | 'validating' | 'ready' | 'failed'

export type TestCasePrepCase = {
  groupId: string
  id: string
  scenario: string
  status: TestCasePrepCaseStatus
  /** 本条生成任务自身的执行类型；抽屉逐条展示标签，节奏也按条读取。 */
  taskType: TestCaseGenerationTaskType
  title: string
}

/** 由稳定用例基线生成后台顺序队列：按业务场景依次排队，供生成引擎与任务抽屉共同消费。 */
export function createTestCaseQueue(taskType: TestCaseGenerationTaskType): TestCasePrepCase[] {
  return TEST_CASE_BLUEPRINTS.map((blueprint) => ({
    groupId: blueprint.groupId,
    id: blueprint.id,
    scenario: blueprint.scenario,
    status: 'queued',
    taskType,
    title: blueprint.title
  }))
}

export type TestCaseDefect = {
  id: string
  severity: '一般' | '严重'
  status: 'open' | 'repairing' | 'resolved'
  summary: string
  target: string
  title: string
}

export type TestCaseExecutionSnapshot = {
  /** 当前等待授权或正在执行的用例；目录据此显示紫色进行中状态。 */
  activeCaseId?: string
  completed: number
  defects?: Record<string, TestCaseDefect[]>
  results?: Record<string, 'pending' | 'running' | 'passed' | 'failed'>
  status: 'idle' | 'running' | 'passed' | 'failed'
  total: number
}

export type TestCasePreparationSnapshot = {
  /** 顺序队列：与分组无关的后台生成次序，供任务抽屉逐条展示生成状态。 */
  cases: TestCasePrepCase[]
  generated: number
  groups: TestCaseGroup[]
  status: TestCasePreparationStatus
  /** 本批生成任务的执行类型；计划确认建队时写入，仅 waiting-plan 阶段为空。 */
  taskType?: TestCaseGenerationTaskType
  total: number
  updatedAt: number
}

/** 创建测试用例准备初始快照：计划未确认前用例数量未知，队列保持为空。 */
export function createInitialTestCasePreparation(): TestCasePreparationSnapshot {
  const groups: TestCaseGroup[] = TEST_CASE_ESTIMATE_GROUPS.map((group) => ({
    id: group.id,
    label: group.label,
    generated: 0,
    total: group.total,
    status: 'queued'
  }))
  return {
    cases: [],
    generated: 0,
    groups,
    status: 'waiting-plan',
    total: 0,
    updatedAt: Date.now()
  }
}

/** 返回顶部阶段条使用的紧凑测试准备状态。 */
export function testCasePreparationLabel(snapshot: TestCasePreparationSnapshot): string {
  if (snapshot.status === 'waiting-plan') return '等待计划'
  if (snapshot.status === 'queued') return '排队中'
  if (snapshot.status === 'generating') return `${snapshot.generated}/${snapshot.total}`
  if (snapshot.status === 'validating') return '校验中'
  if (snapshot.status === 'ready') return '已就绪'
  if (snapshot.status === 'failed') return '生成失败'
  return '已失效'
}
