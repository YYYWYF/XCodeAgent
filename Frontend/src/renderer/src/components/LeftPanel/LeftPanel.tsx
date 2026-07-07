import { Layout } from 'antd';
import type { ApplicationConfig, EditorMode } from '../../typings';
import { cx } from '../../utils';
import AiChatPanel from '../AiChatPanel/AiChatPanel';
import './LeftPanel.less';

const { Sider } = Layout;

type Props = {
  application: ApplicationConfig;
  editorMode: EditorMode;
  onReturnWelcome: () => void;
};

export default function LeftPanel({ application, editorMode, onReturnWelcome }: Props) {
  return (
    <div className={cx('left-panel-wrapper')}>
      <Sider width="100%" className={cx('workbench-pane', 'workbench-left')}>
        <div className={cx('pane-content')}>
          <AiChatPanel
            application={application}
            editorMode={editorMode}
            onReturnWelcome={onReturnWelcome}
          />
        </div>
      </Sider>
    </div>
  );
}
