// 演示数据统一入口：三个独立 PMS 应用场景 + 按 workspace 路由。
// 换演示案例 = 替换 mock-data/ 下三应用目录内容，脚本按 workspaceRoot 取对应场景数据。
import type { ApplicationConfig, ApplicationLifecycle, EditorMode } from '../src/renderer/src/typings'
import type { DevelopmentPlanningApiContract } from '../src/renderer/src/typings'

// —— pms-dev 开发镜像（最近应用 · 开发阶段）——
import { pmsDevApplication, WORKSPACE_ROOT as DEV_ROOT } from './pms-dev/application'
import { pmsDevLifecycle } from './pms-dev/lifecycle'
import { mockPlanningArtifacts as devArtifacts } from './pms-dev/planning-artifacts'
import { WORKBENCH_PAGES as devPages } from './pms-dev/workbench-pages'
import { mockChatSessions as devSessions } from './pms-dev/chat-sessions'
import devRequirementSpec from './pms-dev/requirement-spec.json'
import devProjectPlan from './pms-dev/project-plan.json'
import devClarification from './pms-dev/clarification-questions.json'
import devBuildTaskPlan from './pms-dev/build-task-plan.json'
import devPageDesigns from './pms-dev/page-designs.json'
import devDesignedPageDesigns from './pms-dev/designed-page-designs.json'
import devEndpointDesigns from './pms-dev/endpoint-designs.json'

// —— pms-design 设计镜像（最近应用 · 设计阶段）——
import { pmsDesignApplication, WORKSPACE_ROOT as DESIGN_ROOT } from './pms-design/application'
import { pmsDesignLifecycle } from './pms-design/lifecycle'
import { mockPlanningArtifacts as designArtifacts } from './pms-design/planning-artifacts'
import { WORKBENCH_PAGES as designPages } from './pms-design/workbench-pages'
import { mockChatSessions as designSessions } from './pms-design/chat-sessions'
import designRequirementSpec from './pms-design/requirement-spec.json'
import designProjectPlan from './pms-design/project-plan.json'
import designClarification from './pms-design/clarification-questions.json'
import designBuildTaskPlan from './pms-design/build-task-plan.json'
import designPageDesigns from './pms-design/page-designs.json'
import designDesignedPageDesigns from './pms-design/designed-page-designs.json'
import designEndpointDesigns from './pms-design/endpoint-designs.json'

// —— pms-new 新建主旅程（需求不完善，兜底场景）——
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
  return { app, workspaceRoot, lifecycle, planningArtifacts, workbenchPages, requirementSpec, projectPlan, clarificationQuestions, buildTaskPlan, pageDesigns, designedPageDesigns, endpointDesigns, chatSessions }
}

const SCENARIOS: AppScenario[] = [
  scenario(pmsDevApplication, DEV_ROOT, pmsDevLifecycle, devArtifacts as PlanningArtifactsShape, devPages, devRequirementSpec as Record<string, unknown>, devProjectPlan as Record<string, unknown>, devClarification as Array<Record<string, unknown>>, devBuildTaskPlan as Record<string, unknown>, devPageDesigns as Record<string, unknown>, devDesignedPageDesigns as Record<string, unknown>, devEndpointDesigns as Record<string, unknown>, devSessions),
  scenario(pmsDesignApplication, DESIGN_ROOT, pmsDesignLifecycle, designArtifacts as PlanningArtifactsShape, designPages, designRequirementSpec as Record<string, unknown>, designProjectPlan as Record<string, unknown>, designClarification as Array<Record<string, unknown>>, designBuildTaskPlan as Record<string, unknown>, designPageDesigns as Record<string, unknown>, designDesignedPageDesigns as Record<string, unknown>, designEndpointDesigns as Record<string, unknown>, designSessions)
]

// pms-new 作新建应用兜底场景（新 workspace 路由不到时回退）。
const NEW_SCENARIO: AppScenario = scenario(pmsNewApplication, NEW_ROOT, pmsNewLifecycle, newArtifacts as PlanningArtifactsShape, newPages, newRequirementSpec as Record<string, unknown>, newProjectPlan as Record<string, unknown>, newClarification as Array<Record<string, unknown>>, newBuildTaskPlan as Record<string, unknown>, newPageDesigns as Record<string, unknown>, newDesignedPageDesigns as Record<string, unknown>, newEndpointDesigns as Record<string, unknown>, newSessions)

/** 最近项目列表：两个镜像（设计 / 开发）。 */
export const mockApplications: ApplicationConfig[] = [pmsDesignApplication, pmsDevApplication]

/** 按 workspaceRoot 路由到对应应用场景；未知 workspace 回退 pms-new（新建应用）。 */
export function appDataByWorkspace(workspaceRoot?: string): AppScenario {
  const hit = SCENARIOS.find((s) => s.workspaceRoot === workspaceRoot)
  return hit || NEW_SCENARIO
}

/** 新建应用默认场景（pms-new）。 */
export function newAppScenario(): AppScenario {
  return NEW_SCENARIO
}

/** 供旧 fixtures 兼容：应用列表与默认应用。 */
export const mockWorkspaceApplication = { application: pmsDevApplication.schema }
export const mockLifecycle = pmsDevLifecycle
export { pmsDevApplication, pmsDesignApplication, pmsNewApplication }
