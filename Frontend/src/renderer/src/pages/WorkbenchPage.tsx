import { Layout } from 'antd';
import { useEffect, useState } from 'react';
import { LeftPanel } from '../components';
import { inspectWorkspacePlanningArtifacts, loadWorkspaceApplicationConfig } from '../service/applicationStorage';
import type {
  ApplicationConfig,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  EditorMode
} from '../typings';
import { cx } from '../utils';

type Props = {
  application: ApplicationConfig;
  onReturnWelcome: () => void;
};

type Theme = 'light' | 'dark';

const THEME_PREFERENCE_KEY = 'xcode-agent-theme-preference';

function getTheme(): Theme {
  const storedPreference = window.localStorage.getItem(THEME_PREFERENCE_KEY);
  return storedPreference === 'light' || storedPreference === 'dark' ? storedPreference : 'light';
}

// 组织工作台状态，并以正式 ProjectPlan 页面清单驱动首个页面规划选择。
function WorkbenchPage({ application, onReturnWelcome }: Props) {
  const editorMode: EditorMode = 'frontend';
  const [theme, setTheme] = useState<Theme>(getTheme);
  const [workspaceApplication, setWorkspaceApplication] = useState(application);
  const [developmentPlanningPagesLoaded, setDevelopmentPlanningPagesLoaded] = useState(false);
  const [hasPageDesigns, setHasPageDesigns] = useState(false);
  const [developmentPlanningPages, setDevelopmentPlanningPages] = useState<DevelopmentPlanningPageOption[]>([]);
  const [developmentPlanningApiContracts, setDevelopmentPlanningApiContracts] = useState<DevelopmentPlanningApiContract[]>([]);
  const [planningRefreshRevision, setPlanningRefreshRevision] = useState(0);

  useEffect(() => {
    let active = true;

    // 同步可选的应用配置，并独立读取规划产物及其中的页面清单。
    const syncWorkspaceApplication = async (): Promise<void> => {
      if (!application.workspaceRoot) {
        setDevelopmentPlanningPagesLoaded(true);
        return;
      }
      try {
        const applicationConfig = await loadWorkspaceApplicationConfig(application.workspaceRoot);
        if (!active) return;
        setWorkspaceApplication({
          ...application,
          ...applicationConfig,
          schema: { ...application.schema, ...applicationConfig }
        });
      } catch (error) {
        console.warn('读取工作区 application.json 失败，继续使用已保存应用配置。', error);
      }
      try {
        const inspection = await inspectWorkspacePlanningArtifacts(application.workspaceRoot);
        if (!active) return;
        setDevelopmentPlanningPages(inspection.pages);
        setDevelopmentPlanningApiContracts(
          Array.isArray(inspection.apiContracts) ? inspection.apiContracts : []
        );
        setHasPageDesigns(inspection.hasPageDesigns);
        if (!inspection.ready) {
          console.warn('工作区规划产物不完整。', inspection);
        }
      } catch (error) {
        if (!active) return;
        setDevelopmentPlanningPages([]);
        setDevelopmentPlanningApiContracts([]);
        setHasPageDesigns(false);
        console.warn('检查 specs/plans 规划产物失败。', error);
      } finally {
        if (active) setDevelopmentPlanningPagesLoaded(true);
      }
    };

    setWorkspaceApplication(application);
    setDevelopmentPlanningPagesLoaded(false);
    setHasPageDesigns(false);
    setDevelopmentPlanningPages([]);
    setDevelopmentPlanningApiContracts([]);
    void syncWorkspaceApplication();
    window.addEventListener('focus', syncWorkspaceApplication);
    return () => {
      active = false;
      window.removeEventListener('focus', syncWorkspaceApplication);
    };
  }, [application, planningRefreshRevision]);

  const handleThemeChange = (nextTheme: Theme): void => {
    setTheme(nextTheme);
    window.localStorage.setItem(THEME_PREFERENCE_KEY, nextTheme);
  };

  const handleApplicationUpdate = (updatedApplication: ApplicationConfig): void => {
    setWorkspaceApplication(updatedApplication);
  };

  // 页面设计运行结束后重新读取 pages 目录，避免用内存状态推测是否已经持久化。
  const handlePlanningArtifactsRefresh = (): void => {
    setPlanningRefreshRevision((current) => current + 1);
  };

  return (
    <Layout className={cx('workbench-shell')} data-theme={theme}>
      <LeftPanel
        application={workspaceApplication}
        developmentPlanningReady={developmentPlanningPagesLoaded}
        hasPageDesigns={hasPageDesigns}
        developmentPlanningPages={developmentPlanningPages}
        developmentPlanningApiContracts={developmentPlanningApiContracts}
        editorMode={editorMode}
        onApplicationUpdate={handleApplicationUpdate}
        onPlanningArtifactsRefresh={handlePlanningArtifactsRefresh}
        onReturnWelcome={onReturnWelcome}
        onThemeChange={handleThemeChange}
        theme={theme}
      />
    </Layout>
  );
}

export default WorkbenchPage;
