import type {
  ApplicationConfig,
  ApplicationSchemaConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption
} from '../typings';
import {
  clearApplicationActiveSessionCache,
  clearWorkspaceChatSessionCache,
  listChatSessions,
  readChatSession,
} from './chatSessions';
import { clearApplicationWorkbenchState } from '../workbenchPhase';

const STORAGE_KEY = 'xcode-agent-applications';
const LOCAL_FILE_API = '/api/local-applications';
export const APPLICATIONS_CHANGED_EVENT = 'xcode-agent-applications-changed';

// 判断创建规划是否已经完成；工作台内部运行状态不得影响该结果。
export function isApplicationCreationComplete(lifecycle?: ApplicationLifecycle): boolean {
  return lifecycle?.initialization.stage === 'ready_for_workbench';
}

function normalizeApplications(value: unknown): ApplicationConfig[] {
  return Array.isArray(value) ? (value as ApplicationConfig[]) : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function cacheApplications(applications: ApplicationConfig[]) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(applications));
}

// 通知当前渲染窗口重新校验依赖应用索引的派生状态。
function notifyApplicationsChanged() {
  window.dispatchEvent(new Event(APPLICATIONS_CHANGED_EVENT));
}

// 订阅应用索引变化，并返回用于 React effect 清理的取消函数。
export function subscribeApplicationsChanged(listener: () => void): () => void {
  window.addEventListener(APPLICATIONS_CHANGED_EVENT, listener);
  return () => window.removeEventListener(APPLICATIONS_CHANGED_EVENT, listener);
}

export function loadCachedApplications() {
  try {
    const rawValue = window.localStorage.getItem(STORAGE_KEY);
    if (!rawValue) return [];
    return normalizeApplications(JSON.parse(rawValue));
  } catch {
    return [];
  }
}

export async function loadStoredApplications() {
  const electronApplications = window.xcodeAgent?.applications;

  if (electronApplications) {
    try {
      const data = await electronApplications.load();
      const applications = normalizeApplications(data.applications);
      cacheApplications(applications);
      return applications;
    } catch (error) {
      console.warn(error);
    }
  }

  try {
    const response = await fetch(LOCAL_FILE_API);
    if (!response.ok) throw new Error(`Load applications failed: ${response.status}`);

    const data = (await response.json()) as { applications?: unknown };
    const applications = normalizeApplications(data.applications);
    cacheApplications(applications);
    return applications;
  } catch {
    return loadCachedApplications();
  }
}

export async function saveStoredApplications(applications: ApplicationConfig[]) {
  cacheApplications(applications);

  const electronApplications = window.xcodeAgent?.applications;

  if (electronApplications) {
    try {
      await electronApplications.save(applications);
      notifyApplicationsChanged();
      return;
    } catch (error) {
      console.warn(error);
    }
  }

  try {
    const response = await fetch(LOCAL_FILE_API, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ applications }),
    });
    if (!response.ok) throw new Error(`Save applications failed: ${response.status}`);
  } catch (error) {
    // 文件写入仅在本地开发服务中可用，失败时保留 localStorage 兜底。
    console.warn(error);
  }
  notifyApplicationsChanged();
}

// 从首页应用索引中移除指定项目，不会删除工作区中的任何文件。
export async function removeStoredApplication(applicationId: string) {
  const applications = await loadStoredApplications();
  await saveStoredApplications(
    applications.filter((application) => application.id !== applicationId)
  );
}

// 请求桌面主进程删除受 XCodeAgent 管理的真实项目目录。
export async function deleteStoredProject(workspaceRoot: string) {
  const electronApplications = window.xcodeAgent?.applications;
  if (!electronApplications?.deleteProject) {
    throw new Error('当前环境不支持删除本地项目目录');
  }
  await electronApplications.deleteProject({ workspaceRoot });
  clearWorkspaceChatSessionCache(workspaceRoot);
}

// 清理项目删除后仍可能保留在 Chromium 存储中的应用级恢复键和表单草稿。
export async function clearDeletedApplicationClientState(application: ApplicationConfig) {
  const workspaceRoot = application.workspaceRoot?.trim()
  if (!workspaceRoot) return
  const summaries = (
    await Promise.all(
      (['frontend', 'backend'] as const).map((editorMode) =>
        listChatSessions(workspaceRoot, editorMode).catch(() => [])
      )
    )
  ).flat()
  const threadIds = new Set(
    [application.planningThreadId, ...summaries.map((summary) => summary.threadId)].filter(
      (threadId): threadId is string => Boolean(threadId)
    )
  )
  const changeSetIds = new Set<string>()
  await Promise.all(
    summaries.map(async (summary) => {
      const session = await readChatSession(workspaceRoot, summary.editorMode, summary.id).catch(
        () => undefined
      )
      session?.messages.forEach((item) => {
        const changeSetId = item.codeChanges?.id
        if (changeSetId) changeSetIds.add(changeSetId)
      })
    })
  )

  clearWorkspaceChatSessionCache(workspaceRoot)
  clearApplicationActiveSessionCache(application.id)
  clearApplicationWorkbenchState(application.id)
  for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = window.localStorage.key(index)
    if (
      key &&
      [...threadIds].some((threadId) =>
        key.startsWith(`xcodeagent:clarification-draft:${threadId}:`)
      )
    ) {
      window.localStorage.removeItem(key)
    }
  }
  changeSetIds.forEach((changeSetId) => {
    window.sessionStorage.removeItem(`xcodeagent:version-control:deferred:${changeSetId}`)
  })
}

export async function loadWorkspaceApplicationConfig(
  workspaceRoot: string
): Promise<ApplicationSchemaConfig> {
  const workspaceApi = window.xcodeAgent?.workspace;
  if (!workspaceApi?.readApplication) {
    throw new Error('当前环境不支持读取工作区 application.json');
  }

  const result = await workspaceApi.readApplication({ workspaceRoot });
  if (!isRecord(result.application)) {
    throw new Error('工作区 application.json 格式无效');
  }
  return result.application as unknown as ApplicationSchemaConfig;
}

// 检查正式规划产物，并返回页面/API/Endpoint 详细设计是否已有持久化记录。
export async function inspectWorkspacePlanningArtifacts(
  workspaceRoot: string
): Promise<{
  ready: boolean;
  hasPageDesigns: boolean;
  missing: string[];
  invalid: string[];
  pages: DevelopmentPlanningPageOption[];
  pageTree: DevelopmentPlanningPageTreeNode[];
  apiContracts: DevelopmentPlanningApiContract[];
  entities: DevelopmentPlanningEntityOption[];
}> {
  const workspaceApi = window.xcodeAgent?.workspace;
  if (!workspaceApi?.inspectPlanningArtifacts) {
    throw new Error('当前环境不支持检查工作区规划产物');
  }
  return workspaceApi.inspectPlanningArtifacts({ workspaceRoot });
}
