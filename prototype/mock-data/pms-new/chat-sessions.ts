// 武汉分行需求回检系统 · v1.3 已发布项目的全阶段会话历史。
import type { EditorMode, WorkflowRunPayload } from '../../src/renderer/src/typings'
import type {
  ChatSessionMessage,
  ChatSessionRecord
} from '../../src/renderer/src/service/chatSessions'
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
function completedWorkflow(
  threadId: string,
  phase: string,
  messageText: string,
  target?: {
    pageId?: string
    apiContractId?: string
    endpointId?: string
  }
): WorkflowRunPayload {
  const targetState = target
    ? {
        selectedPageId: target.pageId,
        selectedApiContractId: target.apiContractId,
        selectedEndpointId: target.endpointId,
        detailTargetType: target.endpointId ? 'page' : undefined
      }
    : {}
  return {
    runId: `history-${threadId}-${phase}`,
    threadId,
    summary: { phase, status: 'completed', message: messageText },
    events: [{ type: 'workflow.node.completed', nodeName: phase }],
    state: { phase, ...targetState },
    result: { phase, ...targetState }
  } as WorkflowRunPayload
}

/** 返回页面构建 DAG，展示真实任务节点、产物文件与完成统计。 */
function pageBuildSlice(): ProcessStepRecord['buildExecutionSlice'] {
  const tasks = [
    {
      id: 'task-page-filter',
      task_id: 'task-page-filter',
      unit_id: 'page:my-rechecks',
      owner: 'frontend',
      title: '扩展关键词与紧急程度筛选',
      status: 'completed',
      target_files: ['frontend/src/pages/my-rechecks/index.tsx']
    },
    {
      id: 'task-page-table',
      task_id: 'task-page-table',
      unit_id: 'page:my-rechecks',
      owner: 'frontend',
      title: '补充更新时间列与排序',
      status: 'completed',
      target_files: ['frontend/src/pages/my-rechecks/components/RecheckTable.tsx']
    },
    {
      id: 'task-page-export',
      task_id: 'task-page-export',
      unit_id: 'page:my-rechecks',
      owner: 'frontend',
      title: '接入台账导出交互',
      status: 'completed',
      target_files: ['frontend/src/pages/my-rechecks/hooks/useRecheckExport.ts']
    }
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
    {
      id: 'task-api-query',
      task_id: 'task-api-query',
      unit_id: 'endpoint:rechecks:ep-my-rechecks',
      owner: 'backend',
      title: '扩展分页查询参数与校验',
      status: 'completed',
      target_files: ['backend/app/routes/rechecks.py']
    },
    {
      id: 'task-api-scope',
      task_id: 'task-api-scope',
      unit_id: 'endpoint:rechecks:ep-my-rechecks',
      owner: 'backend',
      title: '固化当前用户数据权限',
      status: 'completed',
      target_files: ['backend/app/services/rechecks.py']
    },
    {
      id: 'task-api-export',
      task_id: 'task-api-export',
      unit_id: 'endpoint:rechecks:ep-my-rechecks',
      owner: 'backend',
      title: '补充台账导出查询能力',
      status: 'completed',
      target_files: ['backend/app/repositories/rechecks.py']
    }
  ]
  return {
    scope: { type: 'endpoint', id: 'ep-my-rechecks', label: 'GET /api/rechecks/my' },
    target_unit_ids: ['endpoint:rechecks:ep-my-rechecks'],
    tasks,
    summary: { total: tasks.length, completed: tasks.length, pending: 0, running: 0, failed: 0 }
  } as ProcessStepRecord['buildExecutionSlice']
}

/** 返回“我的回检”唯一研发对话的双产物 DAG，页面任务显式依赖接口实现。 */
function myRechecksBuildSlice(): ProcessStepRecord['buildExecutionSlice'] {
  const endpointTasks = apiBuildSlice()?.tasks || []
  const pageTasks = (pageBuildSlice()?.tasks || []).map((task) => ({
    ...task,
    dependencies: ['task-api-query', 'task-api-scope']
  }))
  const tasks = [
    ...endpointTasks,
    ...pageTasks,
    {
      id: 'task-page-api-integration',
      task_id: 'task-page-api-integration',
      unit_id: 'integration:my-rechecks',
      owner: 'test',
      title: '联调页面与查询接口',
      status: 'completed',
      dependencies: ['task-page-filter', 'task-page-table', 'task-api-query', 'task-api-scope'],
      target_files: [
        'frontend/src/pages/my-rechecks/index.tsx',
        'backend/app/routes/rechecks.py'
      ]
    }
  ]
  return {
    scope: { type: 'page', id: 'my-rechecks', label: '我的回检（页面 + 接口）' },
    target_unit_ids: ['page:my-rechecks', 'endpoint:rechecks:ep-my-rechecks'],
    tasks,
    summary: { total: tasks.length, completed: tasks.length, pending: 0, running: 0, failed: 0 }
  } as ProcessStepRecord['buildExecutionSlice']
}

/** 返回已发布 v1.3 的设计、页面开发、接口开发和审查历史。 */
export function mockChatSessions(
  workspaceRoot: string,
  editorMode: EditorMode
): ChatSessionRecord[] {
  if (editorMode !== 'frontend') return []
  const now = Date.now()
  const day = 86400000
  return [
    {
      id: 'session-review-v1-3',
      title: '代码审查',
      editorMode,
      threadId: 'thread-review-v1-3',
      versionId: VERSION_ID,
      workspaceRoot,
      createdAt: now - day,
      updatedAt: now - 3600000,
      messages: [
        message(
          401,
          'assistant',
          '页面与接口任务均已完成，已自动进入代码审查。',
          now - 5 * 3600000,
          {
            workflow: completedWorkflow('thread-review-v1-3', 'review', '开始应用级代码审查'),
            processSteps: [
              step(
                'review-inspect',
                1,
                '扫描工作区变更',
                '识别 2 个开发任务关联的 2 个页面和 1 个接口产物。'
              ),
              step(
                'review-lint',
                2,
                '执行代码规范检测',
                'TypeScript、Python 格式及重复代码检测通过。'
              ),
              step(
                'review-security',
                3,
                '检查权限与输入边界',
                '确认填报人身份取自登录上下文，查询参数均经过枚举和长度校验。'
              ),
              step('review-tests', 4, '运行生成版本前验证', '', {
                checks: [
                  {
                    id: 'review-unit',
                    name: '单元测试',
                    status: 'passed',
                    required: true,
                    evidence: '18/18 通过。'
                  },
                  {
                    id: 'review-contract',
                    name: '接口契约校验',
                    status: 'passed',
                    required: true,
                    evidence: '查询和导出字段一致。'
                  },
                  {
                    id: 'review-health',
                    name: '应用健康检查',
                    status: 'passed',
                    required: true,
                    evidence: '前后端服务均正常。'
                  }
                ]
              })
            ]
          }
        ),
        message(
          402,
          'assistant',
          '审查发现的空关键词归一化问题已在节点内修复并复验。代码规范、安全检查和健康度均通过，v1.3 满足生成版本条件。',
          now - 3600000,
          {
            workflow: completedWorkflow(
              'thread-review-v1-3',
              'finalize_project',
              '审查完成，可以生成版本'
            )
          }
        )
      ]
    },
    {
      id: 'session-feature-my-rechecks',
      title: '实现我的回检',
      editorMode,
      threadId: 'thread-feature-my-rechecks',
      pageId: 'my-rechecks',
      apiContractId: 'rechecks',
      endpointId: 'ep-my-rechecks',
      endpointLabel: 'GET /api/rechecks/my',
      versionId: VERSION_ID,
      workspaceRoot,
      createdAt: now - 3 * day,
      updatedAt: now - 2 * day,
      messages: [
        message(
          301,
          'user',
          '完成“我的回检”查询与列表展示，同时实现页面依赖的 GET /api/rechecks/my；增加关键词、紧急程度、更新时间排序和台账导出。',
          now - 3 * day
        ),
        message(
          302,
          'assistant',
          '已将页面和接口纳入同一任务：接口统一处理查询条件与当前用户数据权限，页面负责筛选、列表和导出交互。',
          now - 3 * day + 180000,
          {
            workflow: completedWorkflow(
              'thread-feature-my-rechecks',
              'detail_confirmation',
              '页面与接口设计已确认',
              {
                pageId: 'my-rechecks',
                apiContractId: 'rechecks',
                endpointId: 'ep-my-rechecks'
              }
            ),
            processSteps: [
              step(
                'feature-design-context',
                1,
                '读取双产物设计上下文',
                '读取需求文档、项目计划和既有查询契约，确认本对话拥有页面与接口两个产物。'
              ),
              step(
                'feature-design-api',
                2,
                '设计 GET /api/rechecks/my',
                '定义筛选、排序、导出参数以及当前用户数据权限边界。'
              ),
              step(
                'feature-design-page',
                3,
                '设计“我的回检”页面',
                '页面筛选区、列表和导出操作全部消费已确认的接口契约。'
              ),
              step(
                'feature-design-check',
                4,
                '校验页面调用关系',
                '确认页面字段、请求参数和异常反馈与 GET /api/rechecks/my 完全一致。'
              )
            ]
          }
        ),
        message(303, 'user', '设计确认，开始构建并完成联调。', now - 2.8 * day),
        message(304, 'assistant', '页面、查询接口与联调验证已全部完成。', now - 2.2 * day, {
          workflow: completedWorkflow(
            'thread-feature-my-rechecks',
            'integration_test',
            '跨产物开发任务完成',
            {
              pageId: 'my-rechecks',
              apiContractId: 'rechecks',
              endpointId: 'ep-my-rechecks'
            }
          ),
          processSteps: [
            step(
              'feature-inspect',
              1,
              '汇总页面与接口上下文',
              '读取页面设计、接口契约和当前用户数据权限约束。'
            ),
            step(
              'feature-build',
              2,
              '执行页面与接口构建子图',
              '同一 DAG 先完成查询接口，再让页面按契约接入，最后执行跨产物联调。',
              {
                buildExecutionSlice: myRechecksBuildSlice()
              }
            ),
            step(
              'feature-test',
              3,
              '执行页面与接口联调',
              '验证页面对 GET /api/rechecks/my 的依赖调用与异常边界。',
              {
                checks: [
                  {
                    id: 'api-contract',
                    name: 'API 契约一致性',
                    status: 'passed',
                    required: true,
                    evidence: '筛选、排序与导出字段符合契约。'
                  },
                  {
                    id: 'api-auth',
                    name: '数据权限隔离',
                    status: 'passed',
                    required: true,
                    evidence: '跨用户查询被拒绝。'
                  },
                  {
                    id: 'page-render',
                    name: '页面联调',
                    status: 'passed',
                    required: true,
                    evidence: '筛选、列表与导出均使用真实接口返回。'
                  }
                ]
              }
            )
          ]
        })
      ]
    },
    {
      id: 'session-page-introduction',
      title: '实现回检介绍',
      editorMode,
      threadId: 'thread-page-introduction',
      pageId: 'recheck-introduction',
      versionId: VERSION_ID,
      workspaceRoot,
      createdAt: now - 5 * day,
      updatedAt: now - 3 * day,
      messages: [
        message(
          201,
          'user',
          '实现“回检介绍”静态页面，说明适用场景和填报、审核、整改、归档流程，不需要调用接口。',
          now - 5 * day
        ),
        message(
          202,
          'assistant',
          '已生成页面设计：采用介绍区和四步流程区，底部提供进入“我的回检”的操作入口。',
          now - 4.9 * day,
          {
            workflow: completedWorkflow(
              'thread-page-introduction',
              'detail_confirmation',
              '静态页面设计已确认'
            )
          }
        ),
        message(203, 'user', '页面设计确认，开始构建。', now - 4.7 * day),
        message(204, 'assistant', '回检介绍页面、路由与无接口渲染验证均已完成。', now - 3 * day, {
          workflow: completedWorkflow(
            'thread-page-introduction',
            'integration_test',
            '静态页面开发完成'
          ),
          processSteps: [
            step('intro-inspect', 1, '读取应用设计', '确认页面只承载说明内容，不需要接口或模型。'),
            step('intro-build', 2, '生成静态页面', '完成介绍区、四步流程和回检入口。'),
            step('intro-test', 3, '验证静态页面', '', {
              checks: [
                {
                  id: 'intro-render',
                  name: '页面渲染',
                  status: 'passed',
                  required: true,
                  evidence: '桌面和窄屏布局均正常。'
                },
                {
                  id: 'intro-offline',
                  name: '无接口依赖',
                  status: 'passed',
                  required: true,
                  evidence: '断开后端时页面仍可完整展示。'
                }
              ]
            })
          ]
        })
      ]
    },
    {
      id: 'session-product-design',
      title: '应用设计',
      editorMode,
      threadId: 'thread-product-design-v1-3',
      versionId: VERSION_ID,
      workspaceRoot,
      createdAt: now - 8 * day,
      updatedAt: now - 6 * day,
      messages: [
        message(
          101,
          'user',
          '基于已生成版本的 v1.2 发起迭代。回检单越来越多，需要关键词检索、紧急程度筛选、更新时间展示，并支持按当前条件导出台账。',
          now - 8 * day
        ),
        message(
          102,
          'assistant',
          '已完成需求澄清：本次只扩展“我的回检”和既有查询契约，不新增角色与审批流程；导出必须继承筛选条件和当前用户的数据权限。',
          now - 7.8 * day,
          {
            workflow: completedWorkflow(
              'thread-product-design-v1-3',
              'requirements',
              'v1.3 需求文档已生成'
            ),
            processSteps: [
              step(
                'design-context',
                1,
                '读取 v1.2 版本基线',
                '恢复已生成版本的页面、接口契约与数据权限约束。'
              ),
              step('design-diff', 2, '分析迭代差异', '识别 4 项体验增量和 1 项权限不变约束。'),
              step('design-spec', 3, '生成需求文档', '形成可验收的 v1.3 增量需求与非目标。')
            ]
          }
        ),
        message(103, 'user', '需求文档确认，继续生成项目计划。', now - 7.4 * day),
        message(
          104,
          'assistant',
          '项目计划已生成：共 2 个页面和 1 个接口，组织为“实现回检介绍”和“实现我的回检”两个开发对话。',
          now - 7 * day,
          {
            workflow: completedWorkflow(
              'thread-product-design-v1-3',
              'project_planning',
              'v1.3 项目计划已确认'
            ),
            processSteps: [
              step(
                'plan-units',
                1,
                '识别应用产物',
                '确认回检介绍、我的回检和查询接口共 3 个产物。'
              ),
              step(
                'plan-deps',
                2,
                '组织开发目标',
                '静态介绍页独立实现；查询页面与接口在同一任务中构建并联调。'
              )
            ]
          }
        ),
        message(105, 'user', '项目计划确认，进入开发阶段。', now - 6.5 * day),
        message(
          106,
          'assistant',
          '需求文档与项目计划均已确认。两个开发对话覆盖 2 个页面和 1 个接口，全部完成后会自动进入代码审查。',
          now - 6 * day
        )
      ]
    }
  ]
}
