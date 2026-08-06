import React from 'react';
import ReactDOM from 'react-dom/client';
import 'antd/dist/antd.less';
import './styles/global.less';
import { WorkbenchProvider } from './context';
import { AppEntryPage } from './pages';
import { AuthenticationFailureGate } from './components/AuthenticationFailureGate/AuthenticationFailureGate';
import { ApplicationThemeProvider } from './hooks/useApplicationTheme';
import { loadApplicationTheme } from './service/applicationSettings';

// 标记当前桌面系统，让字体回退可以按平台选择最合适的系统字体。
function markRuntimePlatform(): void {
  const platform = window.navigator.platform.toLowerCase();
  document.documentElement.dataset.platform = platform.startsWith('win')
    ? 'windows'
    : platform.startsWith('mac')
      ? 'macos'
      : 'other';
}

/** 在首次渲染前加载应用级主题，避免界面先显示默认主题再闪烁切换。 */
async function bootstrapRenderer(): Promise<void> {
  markRuntimePlatform();
  await loadApplicationTheme();
  ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
    <React.StrictMode>
      <ApplicationThemeProvider>
        <AuthenticationFailureGate>
          <WorkbenchProvider>
            <AppEntryPage />
          </WorkbenchProvider>
        </AuthenticationFailureGate>
      </ApplicationThemeProvider>
    </React.StrictMode>,
  );
}

void bootstrapRenderer();
