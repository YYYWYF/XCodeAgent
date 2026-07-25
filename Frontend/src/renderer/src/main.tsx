import React from 'react';
import ReactDOM from 'react-dom/client';
import 'antd/dist/antd.less';
import './styles/global.less';
import { WorkbenchProvider } from './context';
import { AppEntryPage } from './pages';
import { AuthenticationFailureGate } from './components/AuthenticationFailureGate/AuthenticationFailureGate';

// 标记当前桌面系统，让字体回退可以按平台选择最合适的系统字体。
function markRuntimePlatform(): void {
  const platform = window.navigator.platform.toLowerCase();
  document.documentElement.dataset.platform = platform.startsWith('win')
    ? 'windows'
    : platform.startsWith('mac')
      ? 'macos'
      : 'other';
}

markRuntimePlatform();

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <AuthenticationFailureGate>
      <WorkbenchProvider>
        <AppEntryPage />
      </WorkbenchProvider>
    </AuthenticationFailureGate>
  </React.StrictMode>,
);
