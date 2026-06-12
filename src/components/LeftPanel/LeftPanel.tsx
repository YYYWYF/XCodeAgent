import { MessageOutlined, SettingOutlined } from '@ant-design/icons';
import { Button, Layout, Tooltip } from 'antd';
import { type ForwardedRef, forwardRef } from 'react';
import type { EditorMode, LeftMode } from '../../typings';
import { cx } from '../../utils';
import AiChatPanel from '../AiChatPanel/AiChatPanel';
import PlaceholderPanel from '../PlaceholderPanel/PlaceholderPanel';
import './LeftPanel.less';

const { Sider } = Layout;

type Props = {
  editorMode: EditorMode;
  leftMode: LeftMode;
  onLeftModeChange: (mode: LeftMode) => void;
  collapsed: boolean;
  dragging: boolean;
};

function LeftPanelInner(
  { editorMode, leftMode, onLeftModeChange, collapsed, dragging }: Props,
  ref: ForwardedRef<HTMLDivElement>,
) {
  return (
    <div
      ref={ref}
      className={cx(
        'left-panel-wrapper',
        collapsed && 'collapsed',
        dragging && 'dragging',
      )}
    >
      <Sider width="100%" className={cx('workbench-pane', 'workbench-left')}>
        <nav className={cx('activity-bar', 'left-activity-bar')} aria-label="左侧工作区切换">
          <Tooltip placement="right" title={`${editorMode === 'frontend' ? '前端' : '后端'} AI 对话`}>
            <Button
              aria-label={`${editorMode === 'frontend' ? '前端' : '后端'} AI 对话`}
              className={cx('activity-button', leftMode === 'chat' && 'active')}
              icon={<MessageOutlined />}
              onClick={() => onLeftModeChange('chat')}
              type="text"
            />
          </Tooltip>
          <Tooltip
            placement="right"
            title={`${editorMode === 'frontend' ? '前端可视化配置' : '后端服务配置'}`}
          >
            <Button
              aria-label={editorMode === 'frontend' ? '前端可视化配置' : '后端服务配置'}
              className={cx('activity-button', leftMode === 'config' && 'active')}
              icon={<SettingOutlined />}
              onClick={() => onLeftModeChange('config')}
              type="text"
            />
          </Tooltip>
        </nav>
        <div className={cx('pane-content')}>
          {leftMode === 'chat' ? (
            <AiChatPanel editorMode={editorMode} />
          ) : (
            <PlaceholderPanel
              title={editorMode === 'frontend' ? '前端可视化配置' : '后端服务配置'}
              description={
                editorMode === 'frontend'
                  ? '当前是前端配置空间，可配置页面结构、组件属性、样式和交互事件。'
                  : '当前是后端配置空间，可配置接口路由、数据模型、服务函数和权限策略。'
              }
            />
          )}
        </div>
      </Sider>
    </div>
  );
}

const LeftPanel = forwardRef(LeftPanelInner);
export default LeftPanel;
