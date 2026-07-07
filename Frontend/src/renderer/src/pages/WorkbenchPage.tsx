import { Layout } from 'antd';
import { LeftPanel } from '../components';
import type { ApplicationConfig, EditorMode } from '../typings';
import { cx } from '../utils';

type Props = {
  application: ApplicationConfig;
  onReturnWelcome: () => void;
};

function WorkbenchPage({ application, onReturnWelcome }: Props) {
  const editorMode: EditorMode = 'frontend';

  return (
    <Layout className={cx('workbench-shell')}>
      <LeftPanel
        application={application}
        editorMode={editorMode}
        onReturnWelcome={onReturnWelcome}
      />
    </Layout>
  );
}

export default WorkbenchPage;
