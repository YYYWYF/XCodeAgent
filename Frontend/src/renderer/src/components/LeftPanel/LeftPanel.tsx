import { Layout } from 'antd';
import type { ApplicationConfig, EditorMode } from '../../typings';
import { cx } from '../../utils';
import AiChatPanel from '../AiChatPanel';
import './LeftPanel.less';

const { Sider } = Layout;

type Props = {
  application: ApplicationConfig;
  editorMode: EditorMode;
  onReturnWelcome: () => void;
  onThemeChange: (theme: 'light' | 'dark' | 'system') => void;
  theme: 'light' | 'dark';
  themePreference: 'light' | 'dark' | 'system';
};

export default function LeftPanel({
  application,
  editorMode,
  onReturnWelcome,
  onThemeChange,
  theme,
  themePreference,
}: Props) {
  return (
    <div className={cx('left-panel-wrapper')}>
      <Sider width="100%" className={cx('workbench-pane', 'workbench-left')}>
        <div className={cx('pane-content')}>
          <AiChatPanel
            application={application}
            editorMode={editorMode}
            onReturnWelcome={onReturnWelcome}
            onThemeChange={onThemeChange}
            theme={theme}
            themePreference={themePreference}
          />
        </div>
      </Sider>
    </div>
  );
}
