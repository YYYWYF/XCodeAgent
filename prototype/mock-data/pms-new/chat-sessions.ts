// 武汉分行需求回检系统 · v1.3 已发布项目的全阶段会话历史。
import type { EditorMode, WorkflowRunPayload } from '../../src/renderer/src/typings'
import type {
  ChatSessionMessage,
  ChatSessionRecord,
  ChatSessionSavedFile
} from '../../src/renderer/src/service/chatSessions'
import type { ProcessStepRecord } from '../../src/renderer/src/service/agUiAgent'
import {
  buildEndpointSource,
  buildPageSource,
  buildProjectPlanDoc,
  buildRequirementSpecDoc,
  buildReviewReport,
  testCaseEstimateFromSource,
  type PageDesign
} from '../../src/renderer/src/workbenchArtifacts'
import { appPath, WORKSPACE_DOC_PATHS } from '../../src/renderer/src/mock/workspaceFiles'
import pageDesigns from './page-designs.json'
import endpointDesigns from './endpoint-designs.json'
import requirementSpec from './requirement-spec.json'
import projectPlan from './project-plan.json'

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

/** 创建历史会话里的已授权文件快照，让发布版本的文件树与产物状态一致。 */
function savedFile(path: string, content: string, savedAt: number): ChatSessionSavedFile {
  return { path, content, savedAt }
}

/** 生成 V1.3 页面代码快照，避免历史会话把未交付的模板卡片误显示出来。 */
function generatedPageFile(pageId: string, savedAt: number): ChatSessionSavedFile {
  const design = (pageDesigns as unknown as Record<string, PageDesign>)[pageId]
  const source = buildPageSource(design, pageId)
  return savedFile(appPath(source.filePath), source.content, savedAt)
}

/** 生成 V1.3 接口代码快照，供发布版本的代码文件树和 Workflow 输出记录共同消费。 */
function generatedEndpointFile(endpointId: string, savedAt: number): ChatSessionSavedFile {
  const design = (endpointDesigns as unknown as Record<string, Record<string, unknown>>)[endpointId]
  const source = buildEndpointSource(design)
  return savedFile(appPath(source.filePath), source.content, savedAt)
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
      title: '补充回检状态筛选',
      status: 'completed',
      target_files: ['frontend/pages/my-rechecks.tsx']
    },
    {
      id: 'task-page-table',
      task_id: 'task-page-table',
      unit_id: 'page:my-rechecks',
      owner: 'frontend',
      title: '补充回检状态展示',
      status: 'completed',
      target_files: ['frontend/pages/my-rechecks.tsx']
    },
    {
      id: 'task-page-empty-state',
      task_id: 'task-page-export',
      unit_id: 'page:my-rechecks',
      owner: 'frontend',
      title: '补充分页与空数据展示',
      status: 'completed',
      target_files: ['frontend/pages/my-rechecks.tsx']
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
      title: '补充分页参数与校验',
      status: 'completed',
      target_files: ['backend/rechecks-controller.java']
    },
    {
      id: 'task-api-scope',
      task_id: 'task-api-scope',
      unit_id: 'endpoint:rechecks:ep-my-rechecks',
      owner: 'backend',
      title: '校验回检查询参数',
      status: 'completed',
      target_files: ['backend/rechecks-controller.java']
    },
    {
      id: 'task-api-pagination',
      task_id: 'task-api-export',
      unit_id: 'endpoint:rechecks:ep-my-rechecks',
      owner: 'backend',
      title: '补充分页查询返回 total',
      status: 'completed',
      target_files: ['backend/rechecks-controller.java']
    }
  ]
  return {
    scope: { type: 'endpoint', id: 'ep-my-rechecks', label: 'GET /api/rechecks/my' },
    target_unit_ids: ['endpoint:rechecks:ep-my-rechecks'],
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
      id: 'session-testing-v1-3',
      title: '应用测试',
      sessionKind: 'testing',
      editorMode,
      threadId: 'thread-testing-v1-3',
      versionId: VERSION_ID,
      workspaceRoot,
      createdAt: now - 1.8 * day,
      updatedAt: now - 1.3 * day,
      messages: [
        message(
          351,
          'assistant',
          '启动、非功能和业务测试均已完成，6 条业务用例全部通过。',
          now - 1.8 * day,
          {
            workflow: completedWorkflow(
              'thread-testing-v1-3',
              'business_test',
              '全部业务测试用例已完成'
            ),
            processSteps: [
              step('testing-startup-v1-3', 1, '启动测试', '确认应用启动、主路由和基础页面加载正常。'),
              step('testing-non-functional-v1-3', 2, '非功能测试', '检查异常反馈、响应稳定性和恢复路径。'),
              step(
                'testing-business-v1-3',
                3,
                '业务测试',
                `完成 ${testCaseEstimateFromSource().total}/${testCaseEstimateFromSource().total} 条业务测试用例验证。`
              )
            ]
          }
        )
      ]
    },
    {
      id: 'session-review-v1-3',
      title: '代码审查',
      sessionKind: 'review',
      editorMode,
      threadId: 'thread-review-v1-3',
      versionId: VERSION_ID,
      workspaceRoot,
      createdAt: now - day,
      updatedAt: now - 3600000,
      savedFiles: [
        {
          path: appPath(WORKSPACE_DOC_PATHS.codeReview),
          content: buildReviewReport({ completed: 6, status: 'passed', total: 6 }),
          savedAt: now - 3600000
        }
      ],
      messages: [
        message(
          401,
          'assistant',
          '页面与接口任务均已完成，代码审查工作流已完成，审查报告已保存。',
          now - 5 * 3600000,
          {
            workflow: completedWorkflow(
              'thread-review-v1-3',
              'finalize_project',
              '审查完成，可以生成版本'
            ),
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
                '检查输入与错误边界',
                '确认查询参数经过格式校验，异常响应能够被页面正确呈现。'
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
                    evidence: '查询字段与页面数据绑定一致。'
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
        )
      ]
    },
    {
      // 当前原型的开发剧本使用一条阶段主对话承接两条产物 Workflow。
      id: 'session-development-v1-3',
      title: '应用开发',
      sessionKind: 'development',
      editorMode,
      threadId: 'thread-development-v1-3',
      versionId: VERSION_ID,
      workspaceRoot,
      savedFiles: [
        generatedPageFile('recheck-introduction', now - 3 * day),
        generatedPageFile('my-rechecks', now - 2.2 * day),
        generatedEndpointFile('ep-my-rechecks', now - 2.2 * day)
      ],
      createdAt: now - 5 * day,
      updatedAt: now - 2 * day,
      messages: [
        message(
          201,
          'user',
          '实现“回检介绍”静态页面，说明适用场景和填报、审核、整改、归档流程，不需要调用接口。',
          now - 5 * day
        ),
        message(204, 'assistant', '回检介绍页面代码已完成并保存。', now - 3 * day, {
          workflow: completedWorkflow(
            'thread-development-v1-3',
            'build',
            '静态页面代码产物已完成',
            { pageId: 'recheck-introduction' }
          ),
          processSteps: [
            step('intro-inspect', 1, '读取需求与计划', '确认页面只承载说明内容，不需要接口或模型。'),
            step('intro-build', 2, '生成静态页面代码', '完成介绍区、四步流程和回检入口。'),
            step('intro-delivery', 3, '交付页面文件', '页面代码已保存，后续启动与业务验证统一在测试阶段执行。')
          ]
        }),
        message(
          301,
          'user',
          '继续实现“我的回检”页面及其 GET /api/rechecks/my 接口，完成查询、筛选和状态跟踪。',
          now - 3 * day
        ),
        message(304, 'assistant', '我的回检页面与查询接口代码产物已完成并保存。', now - 2.2 * day, {
          workflow: completedWorkflow(
            'thread-development-v1-3',
            'build',
            '页面与接口代码产物已完成',
            {
              pageId: 'my-rechecks',
              apiContractId: 'rechecks',
              endpointId: 'ep-my-rechecks'
            }
          ),
          processSteps: [
            step('feature-inspect', 1, '汇总页面与接口上下文', '读取页面设计、接口契约和当前用户数据权限约束。'),
            step('feature-api-build', 2, '实现 GET /api/rechecks/my', '先交付页面依赖的查询接口文件。', {
              buildExecutionSlice: apiBuildSlice()
            }),
            step('feature-page-build', 3, '实现“我的回检”页面', '按已确认的接口契约完成筛选、列表和状态展示。', {
              buildExecutionSlice: pageBuildSlice()
            }),
            step('feature-delivery', 4, '交付页面与接口文件', '页面与接口文件已保存，两个 Workflow 已连续追加到同一主对话。')
          ]
        })
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
      savedFiles: [
        generatedPageFile('my-rechecks', now - 2.2 * day),
        generatedEndpointFile('ep-my-rechecks', now - 2.2 * day)
      ],
      createdAt: now - 3 * day,
      updatedAt: now - 2 * day,
      messages: [
        message(
          301,
          'user',
          '完成“我的回检”查询与列表展示，同时实现页面依赖的 GET /api/rechecks/my；支持状态筛选和回检状态跟踪。',
          now - 3 * day
        ),
        message(
          302,
          'assistant',
          '已确认页面与接口上下文，开始开发工作流。',
          now - 3 * day + 180000,
          {}
        ),
        message(304, 'assistant', '页面与查询接口代码产物已完成，文件已保存。', now - 2.2 * day, {
          workflow: completedWorkflow(
            'thread-feature-my-rechecks',
            'build',
            '页面与接口代码产物已完成',
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
              'feature-api-build',
              2,
              '实现 GET /api/rechecks/my',
              '先交付页面依赖的查询接口文件。',
              { buildExecutionSlice: apiBuildSlice() }
            ),
            step(
              'feature-page-build',
              3,
              '实现“我的回检”页面',
               '按已确认的接口契约完成筛选、列表和状态展示。',
              { buildExecutionSlice: pageBuildSlice() }
            ),
            step(
              'feature-delivery',
              4,
              '交付页面与接口文件',
              '页面与接口文件均已保存，本轮交付由 Workflow 输出记录统一追踪。'
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
      savedFiles: [generatedPageFile('recheck-introduction', now - 3 * day)],
      createdAt: now - 5 * day,
      updatedAt: now - 3 * day,
      messages: [
        message(
          201,
          'user',
          '实现“回检介绍”静态页面，说明适用场景和填报、审核、整改、归档流程，不需要调用接口。',
          now - 5 * day
        ),
        message(204, 'assistant', '回检介绍页面代码已完成并保存。', now - 3 * day, {
          workflow: completedWorkflow(
            'thread-page-introduction',
            'build',
            '静态页面代码产物已完成',
            { pageId: 'recheck-introduction' }
          ),
          processSteps: [
            step('intro-inspect', 1, '读取需求与计划', '确认页面只承载说明内容，不需要接口或模型。'),
            step('intro-build', 2, '生成静态页面代码', '完成介绍区、四步流程和回检入口。'),
            step('intro-delivery', 3, '交付页面文件', '页面代码文件已保存，后续启动与业务验证统一在测试阶段执行。')
          ]
        })
      ]
    },
    {
      id: 'session-product-analysis',
      title: '需求分析',
      sessionKind: 'analysis',
      editorMode,
      threadId: 'thread-product-analysis-v1-3',
      versionId: VERSION_ID,
      workspaceRoot,
      createdAt: now - 8 * day,
      updatedAt: now - 7.8 * day,
      savedFiles: [
        {
          path: appPath(WORKSPACE_DOC_PATHS.requirementSpec),
          content: buildRequirementSpecDoc(
            requirementSpec as unknown as Record<string, unknown>,
            '武汉分行需求回检系统'
          ),
          savedAt: now - 7.8 * day
        }
      ],
      messages: [
        message(
          101,
          'user',
          '基于已生成版本的 v1.2 发起迭代。回检单越来越多，需要按审核状态筛选并持续跟踪处理状态。',
          now - 8 * day
        ),
        message(
          102,
          'assistant',
          '已完成需求澄清：本次只扩展“我的回检”和既有查询契约，不新增登录、角色或审批流程；页面按审核状态筛选并跟踪回检状态。',
          now - 7.8 * day,
          {
            workflow: completedWorkflow(
              'thread-product-analysis-v1-3',
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
              step('design-diff', 2, '分析迭代差异', '识别状态筛选与列表跟踪需求，确认不新增认证功能。'),
              step('design-spec', 3, '生成需求文档', '形成可验收的 v1.3 增量需求与非目标。')
            ]
          }
        )
      ]
    },
    {
      id: 'session-project-plan',
      title: '项目计划',
      sessionKind: 'planning',
      editorMode,
      threadId: 'thread-project-plan-v1-3',
      versionId: VERSION_ID,
      workspaceRoot,
      createdAt: now - 7.4 * day,
      updatedAt: now - 6 * day,
      savedFiles: [
        {
          path: appPath(WORKSPACE_DOC_PATHS.projectPlan),
          content: buildProjectPlanDoc(
            projectPlan as unknown as Record<string, unknown>,
            '武汉分行需求回检系统'
          ),
          savedAt: now - 6 * day
        }
      ],
      messages: [
        message(103, 'user', '需求文档确认，继续生成项目计划。', now - 7.4 * day),
        message(
          104,
          'assistant',
          '项目计划已生成：共 2 个页面和 1 个接口，开发阶段由一个主对话连续承接 2 条 Workflow；计划确认后后台预计生成 6 条业务测试用例。',
          now - 7 * day,
          {
            workflow: completedWorkflow(
              'thread-project-plan-v1-3',
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
          '需求文档与项目计划均已确认。开发主对话将连续承接 2 条页面/接口 Workflow，全部完成后进入测试阶段。',
          now - 6 * day
        )
      ]
    }
  ]
}
