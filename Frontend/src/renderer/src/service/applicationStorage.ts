import type { ApplicationConfig, ApplicationSchemaConfig } from '../typings';

const STORAGE_KEY = 'xcode-agent-applications';
const LOCAL_FILE_API = '/api/local-applications';

function normalizeApplications(value: unknown): ApplicationConfig[] {
  return Array.isArray(value) ? (value as ApplicationConfig[]) : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function cacheApplications(applications: ApplicationConfig[]) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(applications));
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
