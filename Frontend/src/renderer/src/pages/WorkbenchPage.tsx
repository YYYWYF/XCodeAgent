import { Layout } from 'antd';
import { LeftPanel } from '../components';
import type { ApplicationConfig, EditorMode } from '../typings';
import { cx } from '../utils';

type Props = {
  application: ApplicationConfig;
};

function WorkbenchPage({ application }: Props) {
  const editorMode: EditorMode = 'frontend';

  return (
    <Layout className={cx('workbench-shell')}>
      <LeftPanel
        application={application}
        editorMode={editorMode}
      />
    </Layout>
  );
}

export default WorkbenchPage;
