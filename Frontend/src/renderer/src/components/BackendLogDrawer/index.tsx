import {
  BugOutlined,
  CheckOutlined,
  CopyOutlined,
  ReloadOutlined,
  SettingOutlined
} from '@ant-design/icons'
import { Alert, Button, Drawer, Dropdown, Input, Switch } from 'antd'
import { useContext, useEffect, useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import { UiReadVisibility } from '../../hooks/uiReadVisibility'
import { cx } from '../../utils'
import { useServiceLogClipboard } from '../BrowserPreviewPanel/useServiceLogClipboard'
import BackendLogContent from './BackendLogContent'
import { formatLogEntry, logTime, visibleLogEntries } from './backendLogPresentation'
import { useBackendLogs } from './useBackendLogs'
import './index.less'

/** 承载截图中的后端日志操作，组件卸载时取消实时订阅。 */
function BackendLogDrawer({ onClose }: { onClose: () => void }): ReactElement {
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [latestFirst, setLatestFirst] = useState(true)
  const [query, setQuery] = useState('')
  const { snapshot, loading, error, refresh } = useBackendLogs(autoRefresh)
  const { copied, copyError, copyLogs } = useServiceLogClipboard()
  const entries = useMemo(
    () => visibleLogEntries(snapshot?.entries || [], query, latestFirst),
    [snapshot?.entries, query, latestFirst]
  )
  const visibleText = useMemo(() => entries.map(formatLogEntry).join('\n'), [entries])

  return (
    <Drawer
      open
      title={
        <span className="backend-log-title">
          <span>设置</span>
          <span>/</span>
          <strong>实时日志</strong>
        </span>
      }
      placement="right"
      width="75vw"
      onClose={onClose}
      className="backend-log-drawer"
      destroyOnClose
    >
      <Alert
        showIcon
        type="info"
        className="backend-log-notice"
        message={`查看 Python 后端进程日志，便于排查问题。${autoRefresh ? '当前已开启自动刷新，约每秒更新。' : '自动刷新已关闭，可手动刷新日志。'}`}
      />
      <section className="backend-log-panel" aria-labelledby="backend-log-heading">
        <div className="backend-log-heading">
          <h3 id="backend-log-heading">后端日志</h3>
          <div className="backend-log-switches">
            <label htmlFor="backend-log-latest">最新在前</label>
            <Switch id="backend-log-latest" checked={latestFirst} onChange={setLatestFirst} />
            <label htmlFor="backend-log-auto">自动刷新</label>
            <Switch id="backend-log-auto" checked={autoRefresh} onChange={setAutoRefresh} />
          </div>
        </div>
        <div className="backend-log-toolbar">
          <Input.Search
            aria-label="搜索后端日志"
            placeholder="搜索后端日志…"
            allowClear
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <span className="backend-log-updated" role="status">
            {snapshot ? `更新时间：${logTime(snapshot.updatedAt)}` : '等待日志数据'}
          </span>
          <div className="backend-log-buttons">
            <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>
              刷新后端日志
            </Button>
            <Button
              icon={copied ? <CheckOutlined /> : <CopyOutlined />}
              disabled={!visibleText}
              onClick={() => void copyLogs(visibleText)}
            >
              {copied ? '已复制' : '复制后端日志'}
            </Button>
          </div>
        </div>
        <div className="backend-log-path">
          <span>日志文件</span>
          <code>{snapshot?.filePath || '正在获取日志路径…'}</code>
        </div>
        {(error || snapshot?.storageError || copyError) && (
          <Alert
            showIcon
            type="error"
            className="backend-log-error"
            message={error || snapshot?.storageError || copyError}
          />
        )}
        {snapshot?.truncated && (
          <div className="backend-log-window-hint">
            仅展示最近日志（最多 1 MiB / 5000 行），更早内容请查看日志文件。
          </div>
        )}
        <BackendLogContent
          entries={entries}
          query={query}
          latestFirst={latestFirst}
          loading={loading}
        />
      </section>
    </Drawer>
  )
}

/** 在主题按钮右侧提供设置菜单，离开工作台时关闭抽屉及日志读取。 */
export default function WorkbenchSettings(): ReactElement {
  const [open, setOpen] = useState(false)
  const visible = useContext(UiReadVisibility)
  useEffect(() => {
    if (!visible) setOpen(false)
  }, [visible])
  return (
    <>
      <Dropdown
        trigger={['click']}
        placement="bottomRight"
        overlayClassName="backend-log-settings-menu"
        menu={{
          items: [{ key: 'debug', icon: <BugOutlined />, label: '实时日志' }],
          onClick: () => setOpen(true)
        }}
      >
        <button
          type="button"
          aria-label="设置"
          title="设置"
          className={cx('workbench-topbar-theme-toggle')}
        >
          <SettingOutlined />
        </button>
      </Dropdown>
      {visible && open && <BackendLogDrawer onClose={() => setOpen(false)} />}
    </>
  )
}
