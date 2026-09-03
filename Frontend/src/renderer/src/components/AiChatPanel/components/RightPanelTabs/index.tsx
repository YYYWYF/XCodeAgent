import { CloseOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import type { WorkspaceDocKey } from '../../types'
import { workspaceTabIsAvailable } from './tabAvailability'
import './RightPanelTabs.less'

/** 工作区 tab 键：预览/源码/文档/报告/过程/阶段产物 + 设计阶段的正式产物文档。 */
export type WorkspaceTabKey =
  | 'preview'
  | 'source'
  | 'doc'
  | 'test-report'
  | 'review-report'
  | 'outline'
  | 'process'
  | 'stage-output'
  | WorkspaceDocKey

export type WorkspaceTab = {
  key: WorkspaceTabKey
  label: string
  available: boolean
}

type Props = {
  tabs: WorkspaceTab[]
  active: WorkspaceTabKey
  onChange: (key: WorkspaceTabKey) => void
  onClose: () => void
}

/** 右侧工作区的 tab 条：预览始终可用，其余无内容的 tab 灰显。 */
export default function RightPanelTabs({ tabs, active, onChange, onClose }: Props): ReactElement {
  return (
    <header className={cx('workspace-tabs')}>
      <div className={cx('workspace-tabs-list')}>
        {tabs.map((tab) => {
          const available = workspaceTabIsAvailable(tab.key, tab.available)
          return (
            <button
              key={tab.key}
              type="button"
              className={cx('workspace-tab', active === tab.key && 'active')}
              disabled={!available}
              title={available ? tab.label : `${tab.label}（暂无内容）`}
              onClick={() => onChange(tab.key)}
            >
              {tab.label}
            </button>
          )
        })}
      </div>
      <button
        type="button"
        className={cx('workspace-tabs-close')}
        aria-label="关闭右侧面板"
        onClick={onClose}
      >
        <CloseOutlined />
      </button>
    </header>
  )
}
