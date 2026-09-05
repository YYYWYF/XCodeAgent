import type {
  ApplicationConfig,
  ApplicationIndex,
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

/** 校验并过滤应用索引，拒绝任何夹带 application.json 配置字段的记录。 */
function normalizeApplicationIndexes(value: unknown): ApplicationIndex[] {
  if (!Array.isArray(value)) return []
  const allowedKeys = ['id', 'workspaceRoot', 'name', 'lastOpenedAt']
  return value.filter(
    (value): value is ApplicationIndex =>
      Boolean(value) &&
      typeof value === 'object' &&
      Object.keys(value as Record<string, unknown>).every((key) => allowedKeys.includes(key)) &&
      typeof (value as ApplicationIndex).id === 'string' &&
      typeof (value as ApplicationIndex).workspaceRoot === 'string' &&
      typeof (value as ApplicationIndex).name === 'string' &&
      typeof (value as ApplicationIndex).lastOpenedAt === 'number'
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function cacheApplications(applications: ApplicationIndex[]) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(applications));
}

/** 从运行时应用视图提取唯一允许写入 applications.json 的索引字段。 */
export function applicationIndexOf(application: ApplicationConfig): ApplicationIndex {
  const workspaceRoot = application.workspaceRoot?.trim()
  if (!workspaceRoot) throw new Error('应用缺少工作区路径，不能保存到首页索引')
  return {
    id: application.id,
    workspaceRoot,
    name: application.appName.trim() || application.name,
    lastOpenedAt: Date.now()
  }
}

/** 从运行时应用视图剥离首页与界面元数据，得到可写入 application.json 的配置。 */
export function applicationSchemaOf(application: ApplicationConfig): ApplicationSchemaConfig {
  const {
    id: _id,
    name: _name,
    workspaceRoot: _workspaceRoot,
    lastOpenedAt: _lastOpenedAt,
    projectParentPath: _projectParentPath,
    projectDirectoryName: _projectDirectoryName,
    source: _source,
    audience: _audience,
    legacyTheme: _legacyTheme,
    legacyLayout: _legacyLayout,
    enableTabs: _enableTabs,
    pages: _pages,
    defaultPage: _defaultPage,
    hasDynamicRoutes: _hasDynamicRoutes,
    dynamicRouteDescription: _dynamicRouteDescription,
    requirementPlan: _requirementPlan,
    planningThreadId: _planningThreadId,
    ...schema
  } = application
  return schema
}

/** 读取工作区唯一 application.json 后与首页索引组装当前运行时应用视图。 */
async function applicationViewFromIndex(index: ApplicationIndex): Promise<ApplicationConfig> {
  const schema = await loadWorkspaceApplicationConfig(index.workspaceRoot)
  return {
    ...schema,
    ...index,
    name: schema.appName.trim() || index.name,
    source: 'existing-workspace',
    pages: ['工作台'],
    defaultPage: '工作台'
  }
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

export function loadCachedApplications(): ApplicationIndex[] {
  try {
    const rawValue = window.localStorage.getItem(STORAGE_KEY);
    if (!rawValue) return [];
    return normalizeApplicationIndexes(JSON.parse(rawValue));
  } catch {
    return [];
  }
}

/** 读取索引并从每个工作区重新加载 application.json，避免使用缓存配置。 */
export async function loadStoredApplications(): Promise<ApplicationConfig[]> {
  const electronApplications = window.xcodeAgent?.applications;

  if (electronApplications) {
    try {
      const data = await electronApplications.load();
      const indexes = normalizeApplicationIndexes(data.applications);
      cacheApplications(indexes);
      return (await Promise.allSettled(indexes.map(applicationViewFromIndex)))
        .filter(
          (result): result is PromiseFulfilledResult<ApplicationConfig> => result.status === 'fulfilled'
        )
        .map((result) => result.value)
    } catch (error) {
      console.warn(error);
    }
  }

  try {
    const response = await fetch(LOCAL_FILE_API);
    if (!response.ok) throw new Error(`Load applications failed: ${response.status}`);

    const data = (await response.json()) as { applications?: unknown };
    const indexes = normalizeApplicationIndexes(data.applications);
    cacheApplications(indexes);
    return (await Promise.allSettled(indexes.map(applicationViewFromIndex)))
      .filter(
        (result): result is PromiseFulfilledResult<ApplicationConfig> => result.status === 'fulfilled'
      )
      .map((result) => result.value)
  } catch {
    return (await Promise.allSettled(loadCachedApplications().map(applicationViewFromIndex)))
      .filter(
        (result): result is PromiseFulfilledResult<ApplicationConfig> => result.status === 'fulfilled'
      )
      .map((result) => result.value)
  }
}

/** 保存应用索引；调用者不得传入任何 application.json 配置字段。 */
export async function saveStoredApplications(applications: ApplicationIndex[]) {
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
    applications
      .filter((application) => application.id !== applicationId)
      .map(applicationIndexOf)
  );
}

// 请求桌面主进程先完成后端停机门禁，再删除受 XCodeAgent 管理的真实项目目录。
export async function deleteStoredProject(applicationId: string, workspaceRoot: string) {
  const electronApplications = window.xcodeAgent?.applications;
  if (!electronApplications?.deleteProject) {
    throw new Error('当前环境不支持删除本地项目目录');
  }
  await electronApplications.deleteProject({ applicationId, workspaceRoot });
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

/** 将完整配置保存到其所属工作区，applications.json 绝不参与配置写入。 */
export async function saveWorkspaceApplicationConfig(
  workspaceRoot: string,
  application: ApplicationSchemaConfig
): Promise<ApplicationSchemaConfig> {
  const workspaceApi = window.xcodeAgent?.workspace
  if (!workspaceApi?.writeApplication) {
    throw new Error('当前环境不支持保存工作区 application.json')
  }
  const result = await workspaceApi.writeApplication({ workspaceRoot, application })
  if (!isRecord(result.application)) {
    throw new Error('工作区 application.json 保存后格式无效')
  }
  return result.application as unknown as ApplicationSchemaConfig
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
