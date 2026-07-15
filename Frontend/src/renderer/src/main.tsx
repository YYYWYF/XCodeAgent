import React from 'react';
import ReactDOM from 'react-dom/client';
import 'antd/dist/antd.less';
import './styles/global.less';
import { WorkbenchProvider } from './context';
import { AppEntryPage } from './pages';
import { AuthenticationFailureGate } from './components/AuthenticationFailureGate/AuthenticationFailureGate';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <AuthenticationFailureGate>
      <WorkbenchProvider>
        <AppEntryPage />
      </WorkbenchProvider>
    </AuthenticationFailureGate>
  </React.StrictMode>,
);
