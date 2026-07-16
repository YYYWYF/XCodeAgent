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

function WorkbenchPage({ application, onReturnWelcome }: Props) {
  const editorMode: EditorMode = 'frontend';
  const [theme, setTheme] = useState<Theme>(getTheme);
  const [workspaceApplication, setWorkspaceApplication] = useState(application);

  useEffect(() => {
    let active = true;

    const syncWorkspaceApplication = async (): Promise<void> => {
      if (!application.workspaceRoot) return;
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
    };

    setWorkspaceApplication(application);
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

  return (
    <Layout className={cx('workbench-shell')} data-theme={theme}>
      <LeftPanel
        application={workspaceApplication}
        editorMode={editorMode}
        onApplicationUpdate={handleApplicationUpdate}
        onReturnWelcome={onReturnWelcome}
        onThemeChange={handleThemeChange}
        theme={theme}
      />
    </Layout>
  );
}

export default WorkbenchPage;
