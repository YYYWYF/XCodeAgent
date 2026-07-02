import React from 'react';
import ReactDOM from 'react-dom/client';
import 'antd/dist/antd.less';
import './styles/global.less';
import { WorkbenchProvider } from './context';
import { AppEntryPage } from './pages';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <WorkbenchProvider>
      <AppEntryPage />
    </WorkbenchProvider>
  </React.StrictMode>,
);
