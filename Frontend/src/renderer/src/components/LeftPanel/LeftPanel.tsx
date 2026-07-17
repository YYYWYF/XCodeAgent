import { Layout } from 'antd';
import type { ApplicationConfig, DevelopmentPlanningPageOption, EditorMode } from '../../typings';
import { cx } from '../../utils';
import AiChatPanel from '../AiChatPanel';
import './LeftPanel.less';

const { Sider } = Layout;

type Props = {
  application: ApplicationConfig;
  developmentPlanningReady: boolean;
  developmentPlanningRequired: boolean;
  developmentPlanningPages: DevelopmentPlanningPageOption[];
  editorMode: EditorMode;
  onApplicationUpdate: (application: ApplicationConfig) => void;
  onDevelopmentPlanConfirmed: () => Promise<void>;
  onReturnWelcome: () => void;
  onThemeChange: (theme: 'light' | 'dark') => void;
  theme: 'light' | 'dark';
};

export default function LeftPanel({
  application,
  developmentPlanningReady,
  developmentPlanningRequired,
  developmentPlanningPages,
  editorMode,
  onApplicationUpdate,
  onDevelopmentPlanConfirmed,
  onReturnWelcome,
  onThemeChange,
  theme,
}: Props) {
  return (
    <div className={cx('left-panel-wrapper')}>
      <Sider width="100%" className={cx('workbench-pane', 'workbench-left')}>
        <div className={cx('pane-content')}>
          <AiChatPanel
            application={application}
            developmentPlanningReady={developmentPlanningReady}
            developmentPlanningRequired={developmentPlanningRequired}
            developmentPlanningPages={developmentPlanningPages}
            editorMode={editorMode}
            onApplicationUpdate={onApplicationUpdate}
            onDevelopmentPlanConfirmed={onDevelopmentPlanConfirmed}
            onReturnWelcome={onReturnWelcome}
            onThemeChange={onThemeChange}
            theme={theme}
          />
        </div>
      </Sider>
    </div>
  );
}
