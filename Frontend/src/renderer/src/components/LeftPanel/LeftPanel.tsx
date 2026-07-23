import { Layout } from 'antd';
import type {
  ApplicationConfig,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  EditorMode
} from '../../typings';
import { cx } from '../../utils';
import AiChatPanel from '../AiChatPanel';
import './LeftPanel.less';

const { Sider } = Layout;

type Props = {
  application: ApplicationConfig;
  developmentPlanningReady: boolean;
  hasPageDesigns: boolean;
  developmentPlanningPages: DevelopmentPlanningPageOption[];
  developmentPlanningApiContracts: DevelopmentPlanningApiContract[];
  editorMode: EditorMode;
  onApplicationUpdate: (application: ApplicationConfig) => void;
  onReturnWelcome: () => void;
  onThemeChange: (theme: 'light' | 'dark') => void;
  theme: 'light' | 'dark';
};

export default function LeftPanel({
  application,
  developmentPlanningReady,
  hasPageDesigns,
  developmentPlanningPages,
  developmentPlanningApiContracts,
  editorMode,
  onApplicationUpdate,
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
            hasPageDesigns={hasPageDesigns}
            developmentPlanningPages={developmentPlanningPages}
            developmentPlanningApiContracts={developmentPlanningApiContracts}
            editorMode={editorMode}
            onApplicationUpdate={onApplicationUpdate}
            onReturnWelcome={onReturnWelcome}
            onThemeChange={onThemeChange}
            theme={theme}
          />
        </div>
      </Sider>
    </div>
  );
}
