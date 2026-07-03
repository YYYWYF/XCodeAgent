import { LeftOutlined, RightOutlined } from '@ant-design/icons';
import { Button, Tooltip } from 'antd';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { cx } from '../../utils';
import './ResizeHandle.less';

type Props = {
  collapsed: boolean;
  dragging: boolean;
  onDragStart: (e: ReactMouseEvent) => void;
  onToggleCollapse: () => void;
};

export default function ResizeHandle({ collapsed, dragging, onDragStart, onToggleCollapse }: Props) {
  return (
    <div
      className={cx('resize-handle', collapsed && 'collapsed', dragging && 'dragging')}
      onMouseDown={collapsed ? undefined : onDragStart}
    >
      <Tooltip placement="right" title={collapsed ? '展开左侧面板' : '收起左侧面板'}>
        <Button
          className={cx('collapse-toggle')}
          icon={collapsed ? <RightOutlined /> : <LeftOutlined />}
          onClick={onToggleCollapse}
          type="text"
          aria-label={collapsed ? '展开左侧面板' : '收起左侧面板'}
        />
      </Tooltip>
    </div>
  );
}
