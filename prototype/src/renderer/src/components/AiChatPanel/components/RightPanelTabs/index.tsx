import {
  AppstoreOutlined,
  CodeOutlined,
  FileMarkdownOutlined,
  FolderOpenOutlined,
  GlobalOutlined
} from '@ant-design/icons'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import type { WorkspaceDocKey } from '../../types'
import './RightPanelTabs.less'

/** 工作区 tab 键：预览/源码/文档/过程 + 设计阶段的三份产物文档（需求文档/项目计划/构建任务）。 */
export type WorkspaceTabKey =
  | 'preview'
  | 'application-preview'
  | 'page-preview'
  | 'project'
  | 'source'
  | 'doc'
  | 'page-source'
  | 'endpoint-source'
  | 'detail-doc'
  | 'process'
  | WorkspaceDocKey

export type WorkspaceTab = {
  key: WorkspaceTabKey
  label: string
  available: boolean
  icon?: 'application' | 'browser' | 'code' | 'document' | 'project'
}

type Props = {
  tabs: WorkspaceTab[]
  active: WorkspaceTabKey
  onChange: (key: WorkspaceTabKey) => void
}

/** 右侧工作区的 tab 条：预览 / 源码 / 文档 / 过程。不可用的 tab 灰显。 */
export default function RightPanelTabs({
  tabs,
  active,
  onChange
}: Props): ReactElement {
  /** 按窗口内容类型返回统一线性图标。 */
  const renderIcon = (icon?: WorkspaceTab['icon']): ReactElement | null => {
    if (icon === 'application') return <AppstoreOutlined />
    if (icon === 'browser') return <GlobalOutlined />
    if (icon === 'code') return <CodeOutlined />
    if (icon === 'document') return <FileMarkdownOutlined />
    if (icon === 'project') return <FolderOpenOutlined />
    return null
  }

  return (
    <header className={cx('workspace-tabs')}>
      <div className={cx('workspace-tabs-list')}>
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={cx('workspace-tab', active === tab.key && 'active')}
            disabled={!tab.available}
            title={tab.available ? tab.label : `${tab.label}（暂无内容）`}
            onClick={() => onChange(tab.key)}
          >
            {renderIcon(tab.icon)}
            {tab.label}
          </button>
        ))}
      </div>
    </header>
  )
}
