import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../../../utils'
import RightPanelLayoutControl from '../RightPanelLayoutControl'
import RightPanelTabs, { type WorkspaceTab, type WorkspaceTabKey } from '../RightPanelTabs'
import type { RightPanelLayout } from '../../types'

type Props = {
  activeTab: WorkspaceTabKey
  children: ReactNode
  layout: RightPanelLayout
  onLayoutChange: (layout: RightPanelLayout) => void
  onTabChange: (key: WorkspaceTabKey) => void
  tabs: WorkspaceTab[]
}

/** 工作台右侧公共工作区：统一承载 Tab、关闭动作和当前面板内容。 */
export default function WorkbenchRightPanel({
  activeTab,
  children,
  layout,
  onLayoutChange,
  onTabChange,
  tabs
}: Props): ReactElement {
  if (layout === 'hidden') {
    return (
      <RightPanelLayoutControl
        floating
        onChange={onLayoutChange}
        value={layout}
      />
    )
  }

  return (
    <div className={cx('embedded-preview-pane', 'workspace-pane')}>
      <RightPanelLayoutControl docked onChange={onLayoutChange} value={layout} />
      <RightPanelTabs
        tabs={tabs}
        active={activeTab}
        onChange={onTabChange}
      />
      <div className={cx('workspace-content')}>{children}</div>
    </div>
  )
}
