import React from 'react';
import ReactDOM from 'react-dom/client';
import 'antd/dist/antd.less';
import './styles/global.less';
import { WorkbenchProvider } from './context';
import { WorkbenchPage } from './pages';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <WorkbenchProvider>
      <WorkbenchPage />
    </WorkbenchProvider>
  </React.StrictMode>,
);
