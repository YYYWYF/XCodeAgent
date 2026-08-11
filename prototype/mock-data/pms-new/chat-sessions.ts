// 武汉分行需求回检系统 · v1.3 已发布项目的全阶段会话历史。
import type { EditorMode, WorkflowRunPayload } from '../../src/renderer/src/typings'
import type { ChatSessionMessage, ChatSessionRecord } from '../../src/renderer/src/service/chatSessions'
import type { ProcessStepRecord } from '../../src/renderer/src/service/agUiAgent'

const VERSION_ID = 'app-pms-new-v1-3'

/** 创建已归档的真实 Workflow 节点步骤，历史会话会复用正式的 ProcessSteps 卡片渲染。 */
function step(
  id: string,
  sequence: number,
  title: string,
  detail: string,
  extra: Partial<ProcessStepRecord> = {}
): ProcessStepRecord {
  return { id, sequence, title, detail, kind: 'workflow', status: 'completed', ...extra }
}

/** 创建带稳定时间戳和可选 Workflow/节点卡片的历史消息。 */
function message(
  id: number,
  role: ChatSessionMessage['role'],
  content: string,
  createdAt: number,
  options: Partial<Pick<ChatSessionMessage, 'workflow' | 'processSteps'>> = {}
): ChatSessionMessage {
  return { id, role, content, createdAt, ...options }
}

/** 构造完成态 Workflow 投影，使历史消息与实时旅程使用同一数据结构。 */
function completedWorkflow(threadId: string, phase: string, messageText: string): WorkflowRunPayload {
  return {
    runId: `history-${threadId}-${phase}`,
    threadId,
    summary: { phase, status: 'completed', message: messageText },
    events: [{ type: 'workflow.node.completed', nodeName: phase }],
    state: { phase },
    result: { phase }
  } as WorkflowRunPayload
}

/** 返回页面构建 DAG，展示真实任务节点、产物文件与完成统计。 */
function pageBuildSlice(): ProcessStepRecord['buildExecutionSlice'] {
  const tasks = [
    { id: 'task-page-filter', task_id: 'task-page-filter', unit_id: 'page:my-rechecks', owner: 'frontend', title: '扩展关键词与紧急程度筛选', status: 'completed', target_files: ['frontend/src/pages/my-rechecks/index.tsx'] },
    { id: 'task-page-table', task_id: 'task-page-table', unit_id: 'page:my-rechecks', owner: 'frontend', title: '补充更新时间列与排序', status: 'completed', target_files: ['frontend/src/pages/my-rechecks/components/RecheckTable.tsx'] },
    { id: 'task-page-export', task_id: 'task-page-export', unit_id: 'page:my-rechecks', owner: 'frontend', title: '接入台账导出交互', status: 'completed', target_files: ['frontend/src/pages/my-rechecks/hooks/useRecheckExport.ts'] }
  ]
  return {
    scope: { type: 'page', id: 'my-rechecks', label: '我的回检' },
    target_unit_ids: ['page:my-rechecks'],
    tasks,
    summary: { total: tasks.length, completed: tasks.length, pending: 0, running: 0, failed: 0 }
  } as ProcessStepRecord['buildExecutionSlice']
}

/** 返回接口构建 DAG，体现 v1.3 查询条件和数据隔离的后端实现。 */
function apiBuildSlice(): ProcessStepRecord['buildExecutionSlice'] {
  const tasks = [
    { id: 'task-api-query', task_id: 'task-api-query', unit_id: 'endpoint:rechecks:ep-my-rechecks', owner: 'backend', title: '扩展分页查询参数与校验', status: 'completed', target_files: ['backend/app/routes/rechecks.py'] },
    { id: 'task-api-scope', task_id: 'task-api-scope', unit_id: 'endpoint:rechecks:ep-my-rechecks', owner: 'backend', title: '固化当前用户数据权限', status: 'completed', target_files: ['backend/app/services/rechecks.py'] },
    { id: 'task-api-export', task_id: 'task-api-export', unit_id: 'endpoint:rechecks:ep-my-rechecks', owner: 'backend', title: '补充台账导出查询能力', status: 'completed', target_files: ['backend/app/repositories/rechecks.py'] }
  ]
  return {
    scope: { type: 'endpoint', id: 'ep-my-rechecks', label: 'GET /api/rechecks/my' },
    target_unit_ids: ['endpoint:rechecks:ep-my-rechecks'],
    tasks,
    summary: { total: tasks.length, completed: tasks.length, pending: 0, running: 0, failed: 0 }
  } as ProcessStepRecord['buildExecutionSlice']
}

/** 返回已发布 v1.3 的设计、页面开发、接口开发和审查历史。 */
export function mockChatSessions(workspaceRoot: string, editorMode: EditorMode): ChatSessionRecord[] {
  if (editorMode !== 'frontend') return []
  const now = Date.now()
  const day = 86400000
  return [
    {
      id: 'session-review-v1-3', title: '代码审查', editorMode, threadId: 'thread-review-v1-3', versionId: VERSION_ID, workspaceRoot,
      createdAt: now - day, updatedAt: now - 3600000,
      messages: [
        message(401, 'assistant', '页面与接口任务均已完成，已自动进入代码审查。', now - 5 * 3600000, {
          workflow: completedWorkflow('thread-review-v1-3', 'review', '开始应用级代码审查'),
          processSteps: [
            step('review-inspect', 1, '扫描工作区变更', '识别 v1.3 涉及的 9 个文件、3 个前端任务和 3 个后端任务。'),
            step('review-lint', 2, '执行代码规范检测', 'TypeScript、Python 格式及重复代码检测通过。'),
            step('review-security', 3, '检查权限与输入边界', '确认填报人身份取自登录上下文，查询参数均经过枚举和长度校验。'),
            step('review-tests', 4, '运行发布前验证', '', { checks: [
              { id: 'review-unit', name: '单元测试', status: 'passed', required: true, evidence: '18/18 通过。' },
              { id: 'review-contract', name: '接口契约校验', status: 'passed', required: true, evidence: '查询和导出字段一致。' },
              { id: 'review-health', name: '应用健康检查', status: 'passed', required: true, evidence: '前后端服务均正常。' }
            ] })
          ]
        }),
        message(402, 'assistant', '审查发现的空关键词归一化问题已在节点内修复并复验。代码规范、安全检查和健康度均通过，v1.3 满足发布条件。', now - 3600000, {
          workflow: completedWorkflow('thread-review-v1-3', 'finalize_project', '审查完成，可以发布')
        })
      ]
    },
    {
      id: 'session-api-my-rechecks', title: '开发接口：GET /api/rechecks/my', editorMode, threadId: 'thread-api-my-rechecks',
      apiContractId: 'rechecks', endpointId: 'ep-my-rechecks', endpointLabel: 'GET /api/rechecks/my', versionId: VERSION_ID, workspaceRoot,
      createdAt: now - 3 * day, updatedAt: now - 2 * day,
      messages: [
        message(301, 'user', '按 v1.3 详细设计扩展我的回检查询：增加关键词、紧急程度、更新时间排序和台账导出，仍然只能读取当前填报人的数据。', now - 3 * day),
        message(302, 'assistant', '已生成接口详细设计：查询条件进入统一 Query Model，用户编号只从鉴权上下文获取；导出复用同一数据权限过滤器。', now - 3 * day + 180000, { workflow: completedWorkflow('thread-api-my-rechecks', 'detail_confirmation', '接口详细设计已确认') }),
        message(303, 'user', '接口设计确认，开始构建。', now - 2.8 * day),
        message(304, 'assistant', '接口构建与联调已完成。', now - 2.2 * day, {
          workflow: completedWorkflow('thread-api-my-rechecks', 'integration_test', '接口开发完成'),
          processSteps: [
            step('api-inspect', 1, '检查接口上下文', '读取现有契约、路由、服务层与数据权限实现。'),
            step('api-plan', 2, '编译接口构建 DAG', '后端查询、权限与导出任务按依赖排序。'),
            step('api-build', 3, '执行接口构建任务', '', { buildExecutionSlice: apiBuildSlice() }),
            step('api-test', 4, '执行接口集成测试', '', { checks: [
              { id: 'api-contract', name: 'API 契约一致性', status: 'passed', required: true, evidence: '筛选、排序与导出字段符合契约。' },
              { id: 'api-auth', name: '数据权限隔离', status: 'passed', required: true, evidence: '跨用户查询被拒绝。' },
              { id: 'api-edge', name: '参数边界测试', status: 'passed', required: true, evidence: '空关键词、非法枚举与分页上限均覆盖。' }
            ] })
          ]
        })
      ]
    },
    {
      id: 'session-page-my-rechecks', title: '开发页面：我的回检', editorMode, threadId: 'thread-page-my-rechecks',
      pageId: 'my-rechecks', versionId: VERSION_ID, workspaceRoot, createdAt: now - 5 * day, updatedAt: now - 3 * day,
      messages: [
        message(201, 'user', '实现 v1.3 的“我的回检”页面调整：关键词检索、紧急程度筛选、更新时间列和台账导出。', now - 5 * day),
        message(202, 'assistant', '页面需求文档已生成：沿用现有列表骨架，在筛选区增加关键词与紧急程度；表格增加更新时间；导出按钮继承当前筛选条件。', now - 4.9 * day, { workflow: completedWorkflow('thread-page-my-rechecks', 'detail_confirmation', '页面需求文档已确认') }),
        message(203, 'user', '页面设计确认，开始构建。', now - 4.7 * day),
        message(204, 'assistant', '页面代码、接口接入与路由验证均已完成，右侧可以查看 v1.3 应用预览。', now - 3 * day, {
          workflow: completedWorkflow('thread-page-my-rechecks', 'integration_test', '页面开发完成'),
          processSteps: [
            step('page-inspect', 1, '汇总页面上下文', '读取 v1.2 页面结构、v1.3 差异需求与接口契约。'),
            step('page-plan', 2, '规划构建任务（DAG）', '将筛选、表格和导出拆成可并行实现单元。'),
            step('page-build', 3, '生成页面代码', '', { buildExecutionSlice: pageBuildSlice() }),
            step('page-test', 4, '执行页面集成测试', '', { checks: [
              { id: 'page-render', name: '页面渲染校验', status: 'passed', required: true, evidence: '桌面与窄屏布局无异常。' },
              { id: 'page-filter', name: '组合筛选校验', status: 'passed', required: true, evidence: '关键词、状态和紧急程度可组合查询。' },
              { id: 'page-export', name: '导出交互校验', status: 'passed', required: true, evidence: '导出继承当前筛选条件。' }
            ] })
          ]
        })
      ]
    },
    {
      id: 'session-product-design', title: '应用设计：v1.3 迭代', editorMode, threadId: 'thread-product-design-v1-3',
      versionId: VERSION_ID, workspaceRoot, createdAt: now - 8 * day, updatedAt: now - 6 * day,
      messages: [
        message(101, 'user', '基于已发布的 v1.2 发起迭代。回检单越来越多，需要关键词检索、紧急程度筛选、更新时间展示，并支持按当前条件导出台账。', now - 8 * day),
        message(102, 'assistant', '已完成需求澄清：本次只扩展“我的回检”和既有查询契约，不新增角色与审批流程；导出必须继承筛选条件和当前用户的数据权限。', now - 7.8 * day, {
          workflow: completedWorkflow('thread-product-design-v1-3', 'requirements', 'v1.3 需求文档已生成'),
          processSteps: [
            step('design-context', 1, '读取 v1.2 版本基线', '恢复已发布页面、接口契约与数据权限约束。'),
            step('design-diff', 2, '分析迭代差异', '识别 4 项体验增量和 1 项权限不变约束。'),
            step('design-spec', 3, '生成需求文档', '形成可验收的 v1.3 增量需求与非目标。')
          ]
        }),
        message(103, 'user', '需求文档确认，继续生成项目计划。', now - 7.4 * day),
        message(104, 'assistant', '项目计划已生成：更新 1 个页面和 1 个接口，页面筛选与后端查询可并行，导出联调依赖两者完成。', now - 7 * day, {
          workflow: completedWorkflow('thread-product-design-v1-3', 'project_planning', 'v1.3 项目计划已确认'),
          processSteps: [
            step('plan-units', 1, '拆分开发单元', '页面、查询接口与导出联调共 3 个工作单元。'),
            step('plan-deps', 2, '分析任务依赖', '筛选 UI 与查询参数并行，导出联调后置。'),
            step('plan-dag', 3, '生成构建任务 DAG', '形成 6 个可执行任务并完成依赖校验。')
          ]
        }),
        message(105, 'user', '项目计划和构建任务确认，进入开发。', now - 6.5 * day),
        message(106, 'assistant', 'v1.3 设计阶段已完成。页面和接口任务已进入开发队列，全部完成后会自动进入代码审查。', now - 6 * day, { workflow: completedWorkflow('thread-product-design-v1-3', 'prepare_build_tasks', '构建任务已确认') })
      ]
    }
  ]
}
