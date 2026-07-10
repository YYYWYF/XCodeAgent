import { Layout } from 'antd';
import { useEffect, useState } from 'react';
import { LeftPanel } from '../components';
import type { ApplicationConfig, EditorMode } from '../typings';
import { cx } from '../utils';

type Props = {
  application: ApplicationConfig;
  onReturnWelcome: () => void;
};

function WorkbenchPage({ application, onReturnWelcome }: Props) {
  const editorMode: EditorMode = 'frontend';
  const [systemTheme, setSystemTheme] = useState<'light' | 'dark'>(() =>
    window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  );
  const [themePreference, setThemePreference] = useState<'light' | 'dark' | 'system'>(() => {
    const storedPreference = window.localStorage.getItem('xcode-agent-theme-preference');
    return storedPreference === 'light' || storedPreference === 'dark'
      ? storedPreference
      : 'system';
  });
  const theme = themePreference === 'system' ? systemTheme : themePreference;

  useEffect(() => {
    const colorScheme = window.matchMedia('(prefers-color-scheme: light)');
    const syncTheme = (event: MediaQueryListEvent): void => {
      setSystemTheme(event.matches ? 'light' : 'dark');
    };
    colorScheme.addEventListener('change', syncTheme);
    return () => colorScheme.removeEventListener('change', syncTheme);
  }, []);

  const handleThemeChange = (nextTheme: 'light' | 'dark' | 'system'): void => {
    setThemePreference(nextTheme);
    window.localStorage.setItem('xcode-agent-theme-preference', nextTheme);
  };

  return (
    <Layout className={cx('workbench-shell')} data-theme={theme}>
      <LeftPanel
        application={application}
        editorMode={editorMode}
        onReturnWelcome={onReturnWelcome}
        onThemeChange={handleThemeChange}
        theme={theme}
        themePreference={themePreference}
      />
    </Layout>
  );
}

export default WorkbenchPage;
