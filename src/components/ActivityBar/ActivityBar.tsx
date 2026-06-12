import { CodeOutlined, DatabaseOutlined, GlobalOutlined } from '@ant-design/icons';
import { Button, Tooltip } from 'antd';
import type { EditorMode, WorkbenchMode } from '../../typings';
import { cx } from '../../utils';
import './ActivityBar.less';

type Props = {
  editorMode: EditorMode;
  workbenchMode: WorkbenchMode;
  onEditorChange: (mode: EditorMode) => void;
  onWorkbenchModeChange: (mode: WorkbenchMode) => void;
};

export default function ActivityBar({
  editorMode,
  workbenchMode,
  onEditorChange,
  onWorkbenchModeChange,
}: Props) {
  const openEditor = (mode: EditorMode) => {
    // 从全局配置页返回时，同时恢复编辑工作台并切到目标编辑器。
    onEditorChange(mode);
    onWorkbenchModeChange('editor');
  };

  return (
    <nav
      className={cx('activity-bar', 'editor-activity-bar', 'global-activity-bar')}
      aria-label="应用工作区切换"
    >
      <Tooltip placement="right" title="前端编辑器">
        <Button
          aria-label="前端编辑器"
          className={cx(
            'activity-button',
            workbenchMode === 'editor' && editorMode === 'frontend' && 'active',
          )}
          icon={<CodeOutlined />}
          onClick={() => openEditor('frontend')}
          type="text"
        />
      </Tooltip>
      <Tooltip placement="right" title="后端编辑器">
        <Button
          aria-label="后端编辑器"
          className={cx(
            'activity-button',
            workbenchMode === 'editor' && editorMode === 'backend' && 'active',
          )}
          icon={<DatabaseOutlined />}
          onClick={() => openEditor('backend')}
          type="text"
        />
      </Tooltip>
      <div className={cx('activity-bar-spacer')} />
      <Tooltip placement="right" title="应用全局配置">
        <Button
          aria-label="应用全局配置"
          className={cx(
            'activity-button',
            workbenchMode === 'global-config' && 'active',
          )}
          icon={<GlobalOutlined />}
          onClick={() => onWorkbenchModeChange('global-config')}
          type="text"
        />
      </Tooltip>
    </nav>
  );
}
