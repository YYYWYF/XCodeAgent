import { Layout } from 'antd';
import { useState } from 'react';
import { LeftPanel } from '../components';
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

  const handleThemeChange = (nextTheme: Theme): void => {
    setTheme(nextTheme);
    window.localStorage.setItem(THEME_PREFERENCE_KEY, nextTheme);
  };

  return (
    <Layout className={cx('workbench-shell')} data-theme={theme}>
      <LeftPanel
        application={application}
        editorMode={editorMode}
        onReturnWelcome={onReturnWelcome}
        onThemeChange={handleThemeChange}
        theme={theme}
      />
    </Layout>
  );
}

export default WorkbenchPage;
