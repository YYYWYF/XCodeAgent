// 演示数据统一入口：单一 pms-new 新建旅程场景（需求回检单模块），按 workspace 路由。
// 换演示案例 = 替换 mock-data/pms-new/ 内容，脚本按 workspaceRoot 取场景数据。
import type { ApplicationConfig, ApplicationLifecycle, EditorMode } from '../src/renderer/src/typings'
import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity
} from '../src/renderer/src/typings'
import type { DevelopmentPlanningAgent } from '../src/renderer/src/agentDevelopment'

// —— pms-new 新建主旅程（需求回检单模块）——
import { pmsNewApplication, WORKSPACE_ROOT as NEW_ROOT } from './pms-new/application'
import { pmsNewLifecycle } from './pms-new/lifecycle'
import { mockPlanningArtifacts as newArtifacts } from './pms-new/planning-artifacts'
import { WORKBENCH_PAGES as newPages } from './pms-new/workbench-pages'
import { mockChatSessions as newSessions } from './pms-new/chat-sessions'
import newRequirementSpec from './pms-new/requirement-spec.json'
import newProjectPlan from './pms-new/project-plan.json'
import newClarification from './pms-new/clarification-questions.json'
import newBuildTaskPlan from './pms-new/build-task-plan.json'
import newPageDesigns from './pms-new/page-designs.json'
import newDesignedPageDesigns from './pms-new/designed-page-designs.json'
import newEndpointDesigns from './pms-new/endpoint-designs.json'

export type PlanningArtifactsShape = {
  ready: boolean
  hasPageDesigns: boolean
  missing: string[]
  invalid: string[]
  pages: Array<Record<string, unknown>>
  pageTree: Array<Record<string, unknown>>
  apiContracts: DevelopmentPlanningApiContract[]
  entities: DevelopmentPlanningEntity[]
  agents: DevelopmentPlanningAgent[]
}

export type AppScenario = {
  app: ApplicationConfig
  workspaceRoot: string
  lifecycle: ApplicationLifecycle
  planningArtifacts: PlanningArtifactsShape
  workbenchPages: Record<string, { label: string; path: string; purpose: string }>
  requirementSpec: Record<string, unknown>
  projectPlan: Record<string, unknown>
  clarificationQuestions: Array<Record<string, unknown>>
  buildTaskPlan: Record<string, unknown>
  pageDesigns: Record<string, unknown>
  designedPageDesigns: Record<string, unknown>
  endpointDesigns: Record<string, unknown>
  chatSessions: (workspaceRoot: string, editorMode: EditorMode) => unknown[]
}

function scenario(
  app: ApplicationConfig,
  workspaceRoot: string,
  lifecycle: ApplicationLifecycle,
  planningArtifacts: PlanningArtifactsShape,
  workbenchPages: AppScenario['workbenchPages'],
  requirementSpec: Record<string, unknown>,
  projectPlan: Record<string, unknown>,
  clarificationQuestions: Array<Record<string, unknown>>,
  buildTaskPlan: Record<string, unknown>,
  pageDesigns: Record<string, unknown>,
  designedPageDesigns: Record<string, unknown>,
  endpointDesigns: Record<string, unknown>,
  chatSessions: (workspaceRoot: string, editorMode: EditorMode) => unknown[]
): AppScenario {
  return {
    app,
    workspaceRoot,
    lifecycle,
    planningArtifacts,
    workbenchPages,
    requirementSpec,
    projectPlan,
    clarificationQuestions,
    buildTaskPlan,
    pageDesigns,
    designedPageDesigns,
    endpointDesigns,
    chatSessions
  }
}

// 唯一场景：新建应用（需求回检单模块）。
const NEW_SCENARIO: AppScenario = scenario(
  pmsNewApplication,
  NEW_ROOT,
  pmsNewLifecycle,
  newArtifacts as PlanningArtifactsShape,
  newPages,
  newRequirementSpec as Record<string, unknown>,
  newProjectPlan as Record<string, unknown>,
  newClarification as Array<Record<string, unknown>>,
  newBuildTaskPlan as Record<string, unknown>,
  newPageDesigns as Record<string, unknown>,
  newDesignedPageDesigns as Record<string, unknown>,
  newEndpointDesigns as Record<string, unknown>,
  newSessions
)

/** 最近项目列表：单一新建旅程场景。 */
export const mockApplications: ApplicationConfig[] = [pmsNewApplication]

/** 按 workspaceRoot 路由；唯一场景直接返回新建应用数据。 */
export function appDataByWorkspace(_workspaceRoot?: string): AppScenario {
  void _workspaceRoot
  return NEW_SCENARIO
}

/** 新建应用默认场景。 */
export function newAppScenario(): AppScenario {
  return NEW_SCENARIO
}

/** 供旧 fixtures 兼容：应用列表与默认应用。 */
export const mockWorkspaceApplication = { application: pmsNewApplication.schema }
export const mockLifecycle = pmsNewLifecycle
export { pmsNewApplication }
