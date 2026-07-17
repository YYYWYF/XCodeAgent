import { Layout } from 'antd';
import { useEffect, useState } from 'react';
import { LeftPanel } from '../components';
import { loadWorkspaceApplicationConfig } from '../service/applicationStorage';
import type { ApplicationConfig, EditorMode } from '../typings';
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

// 递归检查每个菜单项是否都已有非空开发待办清单。
function hasCompleteDevelopmentTasks(items: ApplicationConfig['menus']['items']): boolean {
  if (!items.length) return false;
  return items.every((item) => Boolean(item.developmentTasks?.length) && hasCompleteDevelopmentTasksForChildren(item.children));
}

// 把没有子菜单视为完成，否则继续递归检查全部子项。
function hasCompleteDevelopmentTasksForChildren(items?: ApplicationConfig['menus']['items']): boolean {
  return !items?.length || hasCompleteDevelopmentTasks(items);
}

function WorkbenchPage({ application, onReturnWelcome }: Props) {
  const editorMode: EditorMode = 'frontend';
  const [theme, setTheme] = useState<Theme>(getTheme);
  const [workspaceApplication, setWorkspaceApplication] = useState(application);
  const [workspaceLoaded, setWorkspaceLoaded] = useState(false);

  useEffect(() => {
    let active = true;

    const syncWorkspaceApplication = async (): Promise<void> => {
      if (!application.workspaceRoot) {
        setWorkspaceLoaded(true);
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
      } finally {
        if (active) setWorkspaceLoaded(true);
      }
    };

    setWorkspaceApplication(application);
    setWorkspaceLoaded(false);
    void syncWorkspaceApplication();
    window.addEventListener('focus', syncWorkspaceApplication);
    return () => {
      active = false;
      window.removeEventListener('focus', syncWorkspaceApplication);
    };
  }, [application]);

  const handleThemeChange = (nextTheme: Theme): void => {
    setTheme(nextTheme);
    window.localStorage.setItem(THEME_PREFERENCE_KEY, nextTheme);
  };

  const handleApplicationUpdate = (updatedApplication: ApplicationConfig): void => {
    setWorkspaceApplication(updatedApplication);
  };

  // 开发计划确认后重新读取工作区配置，确保菜单任务来自最终落盘内容。
  const handleDevelopmentPlanConfirmed = async (): Promise<void> => {
    if (!application.workspaceRoot) return;
    const applicationConfig = await loadWorkspaceApplicationConfig(application.workspaceRoot);
    setWorkspaceApplication((current) => ({
      ...current,
      ...applicationConfig,
      schema: { ...current.schema, ...applicationConfig }
    }));
  };

  const needsDevelopmentPlan = workspaceLoaded && !hasCompleteDevelopmentTasks(workspaceApplication.menus.items);

  return (
    <Layout className={cx('workbench-shell')} data-theme={theme}>
      <LeftPanel
        application={workspaceApplication}
        developmentPlanningReady={workspaceLoaded}
        developmentPlanningRequired={!workspaceLoaded || needsDevelopmentPlan}
        editorMode={editorMode}
        onApplicationUpdate={handleApplicationUpdate}
        onDevelopmentPlanConfirmed={handleDevelopmentPlanConfirmed}
        onReturnWelcome={onReturnWelcome}
        onThemeChange={handleThemeChange}
        theme={theme}
      />
    </Layout>
  );
}

export default WorkbenchPage;
