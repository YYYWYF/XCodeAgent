import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, ReactElement } from 'react'
import {
  DatabaseOutlined,
  FolderOutlined,
  HourglassOutlined,
  MoonOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { Empty, Typography } from 'antd'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import type { ApplicationConfig, DevelopmentPlanningPageOption } from '../../../../typings'
import { cx } from '../../../../utils'
import BrowserPreviewPanel from '../../../BrowserPreviewPanel/BrowserPreviewPanel'
import type { ServiceStatusControl } from '../../../BrowserPreviewPanel/ServiceStatusDrawer'
import {
  previewServiceState,
  shouldAutoStartPreviewService
} from '../../../BrowserPreviewPanel/serviceStatusPolicy'
import RightPanelTabs, { type WorkspaceTab, type WorkspaceTabKey } from '../RightPanelTabs'
import SourcePanel from '../SourcePanel'
import './ReleasedVersionPanel.less'

const { Text } = Typography

type Props = {
  application: ApplicationConfig
  workspaceRoot: string
  /** 预览服务控制句柄；沿用工作台既有接线，不重复建立预览运行时。 */
  serviceControl?: ServiceStatusControl
  previewBaseUrl?: string
  pages?: DevelopmentPlanningPageOption[]
  previewErrorMessage?: string
  /** 该版本的 Git tag：应用文件按它读取该版本当时的文档与源码。 */
  revision?: string
}

const TAB_KEYS = { files: 'source', preview: 'preview' } as const

/** 窄栏顶部入口：工作台侧的抽屉入口，历史版本下只做壳。 */
const RAIL_DRAWERS = [
  { key: 'tasks', label: '任务管理', icon: <SidebarAssetIcon source={freeChatIcon} /> },
  { key: 'async', label: '异步任务', icon: <HourglassOutlined /> },
  { key: 'tide', label: '潮汐任务', icon: <MoonOutlined /> },
  { key: 'datasource', label: '数据来源', icon: <DatabaseOutlined /> }
] as const

/** 窄栏底部入口：与工作台窄栏同形，历史版本下不承载行为。 */
const RAIL_FOOTERS = [
  { key: 'files', label: '文件', icon: <FolderOutlined /> },
  { key: 'skills', label: '技能', icon: <ThunderboltOutlined /> },
  { key: 'settings', label: '设置', icon: <SettingOutlined /> }
] as const

type RailDrawerKey = (typeof RAIL_DRAWERS)[number]['key']

/**
 * 用 SVG 资源当图标：以 currentColor 做遮罩，因此跟随按钮的配色与悬停态。
 * 与工作台窄栏的同类实现同款（那边是 SessionSidebar 的模块私有函数，这里引不到）。
 */
function SidebarAssetIcon({ source }: { source: string }): ReactElement {
  return (
    <span
      aria-hidden="true"
      className={cx('released-version-rail-asset-icon')}
      style={{ '--released-version-rail-icon-source': `url("${source}")` } as CSSProperties}
    />
  )
}

/** 已生成版本的只读视图：不保留历史会话，只提供应用文件与应用预览两个入口。 */
export default function ReleasedVersionPanel({
  application,
  workspaceRoot,
  serviceControl,
  previewBaseUrl,
  pages,
  previewErrorMessage,
  revision
}: Props): ReactElement {
  const [activeTab, setActiveTab] = useState<WorkspaceTabKey>(TAB_KEYS.files)
  const [activeDrawer, setActiveDrawer] = useState<RailDrawerKey | undefined>(undefined)
  const tabs: WorkspaceTab[] = [
    { key: TAB_KEYS.files, label: '应用文件', available: true },
    { key: TAB_KEYS.preview, label: '应用预览', available: true }
  ]
  const serviceStatus = previewServiceState(serviceControl?.snapshot?.runtime)
  // 每个"进入预览 tab"的周期只自动启动一次，避免反复拉起。
  const autoStartRequestedRef = useRef(false)
  const activeDrawerLabel = RAIL_DRAWERS.find((item) => item.key === activeDrawer)?.label

  /**
   * 切到应用预览时把服务拉起来。
   *
   * 预览服务在前端不会自动启动（`usePreviewRuntime` 只轮询状态）；正常流程里它是被
   * 验收阶段的 launch_project 节点拉起的，历史版本没有这一步，于是面板停在
   * "待启动 / about:blank"。切 tab 是明确的"我要看这个应用"意图，在这里启动最合适——
   * 只在应用文件 tab 上翻文件不会白白拉起前后端。
   */
  useEffect(() => {
    if (activeTab !== TAB_KEYS.preview) {
      autoStartRequestedRef.current = false
      return
    }
    if (
      !shouldAutoStartPreviewService({
        activeTabIsPreview: true,
        hasSnapshot: Boolean(serviceControl?.snapshot),
        busy: Boolean(serviceControl?.busy),
        status: serviceStatus,
        alreadyRequested: autoStartRequestedRef.current
      })
    ) {
      return
    }
    autoStartRequestedRef.current = true
    serviceControl?.onRestart()
  }, [activeTab, serviceControl, serviceStatus])

  return (
    <div className={cx('released-version-panel')}>
      {/* 最左侧窄栏：纯图标，文案只在悬停时以 title 提示。
          抽屉入口（顶部）与工作台入口（底部）都不常驻文案，避免占据窄栏宽度。 */}
      <nav className={cx('released-version-rail')} aria-label="工作台入口">
        <div className={cx('session-rail-primary')}>
          {RAIL_DRAWERS.map((item) => (
            <button
              key={item.key}
              aria-expanded={activeDrawer === item.key}
              aria-label={item.label}
              className={cx(activeDrawer === item.key && 'active')}
              // 再次点击同一入口收起，与工作台抽屉的互斥语义一致。
              onClick={() =>
                setActiveDrawer((current) => (current === item.key ? undefined : item.key))
              }
              title={item.label}
              type="button"
            >
              {item.icon}
            </button>
          ))}
        </div>
        <span className={cx('session-rail-divider')} aria-hidden="true" />
        <div className={cx('session-rail-secondary')}>
          {RAIL_FOOTERS.map((item) => (
            <button key={item.key} aria-label={item.label} title={item.label} type="button">
              {item.icon}
            </button>
          ))}
          <span className={cx('session-user')} aria-label="当前用户 S" title="当前用户">
            <span className={cx('session-user-avatar')}>S</span>
          </span>
        </div>
      </nav>

      {/* 抽屉占位：这些入口在后端尚无对应能力，先给出明确的空态而不是假装有内容。 */}
      {activeDrawer ? (
        <aside className={cx('released-version-drawer')} aria-label={activeDrawerLabel}>
          <header className={cx('released-version-drawer-head')}>
            <Text strong>{activeDrawerLabel}</Text>
          </header>
          <div className={cx('released-version-drawer-body')}>
            <Empty
              description={`${activeDrawerLabel}暂未接入`}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          </div>
        </aside>
      ) : null}

      <div className={cx('released-version-main')}>
        {/* 不传 onClose：只读回看没有"关闭面板"语义，回到当前版本由顶部版本选择承担。 */}
        <RightPanelTabs tabs={tabs} active={activeTab} onChange={setActiveTab} />
        <div className={cx('released-version-panel-body')}>
          {activeTab === TAB_KEYS.preview ? (
            <BrowserPreviewPanel
              application={application}
              pages={pages}
              previewBaseUrl={previewBaseUrl}
              serviceControl={serviceControl}
              selectedPagePath="/"
              errorMessage={previewErrorMessage}
            />
          ) : (
            <SourcePanel revision={revision} workspaceRoot={workspaceRoot} />
          )}
        </div>
      </div>
    </div>
  )
}
