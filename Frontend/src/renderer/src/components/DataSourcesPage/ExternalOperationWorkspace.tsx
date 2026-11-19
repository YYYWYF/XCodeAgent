import { ApiOutlined, CloseOutlined } from '@ant-design/icons'
import { Button, Tabs, message } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import type { DataSourceOperation, ExternalApiDataSource } from '../../typings'
import { confirmWorkspaceAction } from '../workspaceDialogs'
import { debugStorageKey, initialDraft, restoreDebug, sameDebugDraft, type Draft } from './debugDraft'
import { publicDraft } from './debugPrivacy'
import { applyDebugImport, debugImportAddress, setupDebug, type DebugImport } from './operationDebugSync'
import {
  applyOperationBasic, buildOperationConfiguration, operationBasic, operationConfigurationSnapshot, operationDraftFromSource,
  prepareOperationBasic, rebaseOperationDraft, sameOperationAddress,
  type OperationBasic, type OperationDraft, type OperationUpdater
} from './operationEditorDraft'
import OperationBasicInfo from './OperationBasicInfo'
import OperationConfigurationPane, { type OperationConfigurationTab } from './OperationConfigurationPane'
import ExternalApiDebug from './ExternalApiDebug'

export type OperationDetailTab = 'basic' | 'edit' | 'debug'

type Props = {
  allowDebug: boolean
  workspaceRoot: string
  theme: 'light' | 'dark'
  source: ExternalApiDataSource
  operation: DataSourceOperation
  saving: boolean
  activeTab: OperationDetailTab
  onTabChange: (tab: OperationDetailTab) => void
  onDirtyChange: (dirty: boolean) => void
  onClose: () => void
  onSave: (update: OperationUpdater) => Promise<DataSourceOperation>
}

/** 为同一个接口协调三个页面的草稿、保存范围和同步流程。 */
export default function ExternalOperationWorkspace({
  allowDebug, workspaceRoot, theme, source, operation, saving, activeTab,
  onTabChange, onDirtyChange, onClose, onSave
}: Props) {
  const [basicDraft, setBasicDraft] = useState<OperationBasic>()
  const [basicError, setBasicError] = useState('')
  const [configuration, setConfiguration] = useState(() => operationDraftFromSource(operation))
  const [configurationTab, setConfigurationTab] = useState<OperationConfigurationTab>('params')
  const [configurationError, setConfigurationError] = useState('')
  const [debugMounted, setDebugMounted] = useState(false)
  const [debugChanged, setDebugChanged] = useState(false)
  const [debugSeed, setDebugSeed] = useState<Draft>()
  const [pendingImport, setPendingImport] = useState<DebugImport>()
  const savedBasic = operationBasic(operation)
  const savedConfiguration = useMemo(() => operationConfigurationSnapshot(operationDraftFromSource(operation)), [operation])
  const basicDirty = basicDraft !== undefined && JSON.stringify(basicDraft) !== JSON.stringify(savedBasic)
  const configurationDirty = operationConfigurationSnapshot(configuration) !== savedConfiguration

  /** 任一页有未保存输入时，关闭详情和切换接口走同一离开保护。 */
  useEffect(() => { onDirtyChange(basicDirty || configurationDirty || Boolean(pendingImport)) },
    [basicDirty, configurationDirty, pendingImport, onDirtyChange])

  /** 打开同接口的调试会话。 */
  function openDebug(): void {
    setDebugMounted(true)
    onTabChange('debug')
  }

  /** 切换主页面只改变当前视图，保留基本信息、接口配置和调试草稿。 */
  function switchTab(key: string): void {
    if (saving) return
    if (key === 'debug' && allowDebug) openDebug()
    else onTabChange(key === 'edit' ? 'edit' : 'basic')
  }

  /** 基本信息和配置都以保存时读到的完整接口为基线。 */
  function saveForCurrentAddress(update: OperationUpdater): Promise<DataSourceOperation> {
    return onSave((latest) => {
      if (!sameOperationAddress(latest, operation)) throw new Error('接口的请求方式或路径已变更，请重新打开详情核对。')
      return update(latest)
    })
  }

  /** 原位启动基本信息编辑，不挂载其他配置。 */
  function openBasic(): void {
    setBasicDraft(savedBasic)
    setBasicError('')
  }

  /** 取消仅放弃基本信息草稿和暂存的同步请求。 */
  function cancelBasic(): void {
    setBasicDraft(undefined)
    setBasicError('')
    setPendingImport(undefined)
  }

  /** 校验路径与待移除参数；用户确认后才保存基本信息。 */
  function saveBasic(): void {
    if (!basicDraft || saving) return
    try {
      setBasicError('')
      const { basic, removed } = prepareOperationBasic(basicDraft, operation)
      if (!removed.length) { void commitBasic(basic); return }
      confirmWorkspaceAction({
        title: '移除已配置的 Path 参数？',
        content: '新路径不再包含 ' + removed.join('、') + '，对应参数及其说明将被移除。同名参数保留。',
        okText: '保存基本信息', cancelText: '继续编辑', onOk: () => commitBasic(basic)
      })
    } catch (cause) {
      setBasicError(cause instanceof Error ? cause.message : '基本信息无效。')
    }
  }

  /** 只提交基本信息，并将另一个页面的草稿迁到已保存的路径。 */
  async function commitBasic(basic: OperationBasic): Promise<void> {
    try {
      const saved = await saveForCurrentAddress((latest) => applyOperationBasic(latest, basic))
      const savedDraft = operationDraftFromSource(saved)
      const next = configurationDirty ? rebaseOperationDraft(configuration, saved) : savedDraft
      setConfiguration(next)
      cancelBasic()
      if (!pendingImport) return
      if (!sameOperationAddress(saved, debugImportAddress(pendingImport))) {
        message.info('已保存的请求方式或路径仍与本次调试不同，未同步配置。')
        return
      }
      const stillDirty = operationConfigurationSnapshot(next) !== operationConfigurationSnapshot(savedDraft)
      importConfiguration(pendingImport, next, stillDirty)
    } catch (cause) {
      setBasicError(cause instanceof Error ? cause.message : '基本信息保存失败。')
    }
  }

  /** 同步到接口配置草稿，保留用户对覆盖未保存内容的决定。 */
  function importConfiguration(snapshot: DebugImport, draft: OperationDraft, warn: boolean): void {
    const next = applyDebugImport(draft, snapshot, source)
    /** 切到本次调用导入的主要内容，不在同步时正式保存。 */
    const apply = (): void => {
      setConfiguration(next)
      setConfigurationError('')
      setConfigurationTab(snapshot.responseSample !== undefined ? 'responseBody' : snapshot.requestSample !== undefined ? 'requestBody' : 'params')
      onTabChange('edit')
    }
    if (!warn) { apply(); return }
    confirmWorkspaceAction({
      title: '同步到当前配置草稿？',
      content: '将更新本次调用涉及的参数名称、类型和可同步的样例，保留其他配置。',
      okText: '同步', cancelText: '取消', onOk: apply
    })
  }

  /** 地址不一致时保留同一调用快照，等待基本信息单独保存。 */
  function syncToEdit(snapshot: DebugImport): void {
    const address = debugImportAddress(snapshot)
    if (sameOperationAddress(address, operation)) {
      importConfiguration(snapshot, configuration, configurationDirty)
      return
    }
    confirmWorkspaceAction({
      title: '本次调试的请求方式或路径不同',
      content: <>
        <div>已保存：<code>{operation.method} {operation.path}</code></div>
        <div>本次调试：<code>{address.method} {address.path}</code></div>
        <p>可以将本次请求方式和路径填入基本信息，保存后继续同步同一次调用的配置。</p>
      </>,
      okText: '去基本信息', cancelText: '取消',
      onOk: () => {
        setPendingImport(snapshot)
        setBasicError('')
        setBasicDraft((current) => ({ ...(current ?? savedBasic), ...address }))
        onTabChange('basic')
      }
    })
  }

  /** 只复制请求配置，调试使用已保存的请求方式和路径。 */
  function syncToDebug(): void {
    try {
      setConfigurationError('')
      const seed = setupDebug(source, operation, configuration)
      /** 按用户操作加载配置草稿，不影响编辑页。 */
      const apply = (): void => {
        setDebugSeed(seed)
        openDebug()
      }
      const changed = debugMounted ? debugChanged : hasStoredDebugChanges(workspaceRoot, source, operation)
      if (!changed) { apply(); return }
      confirmWorkspaceAction({
        title: '替换调试请求草稿？',
        content: '将使用已保存的请求方式和路径，以及此页的请求参数、请求头和请求体样例。已有试填参数会被替换。',
        okText: '同步', cancelText: '取消', onOk: apply
      })
    } catch (cause) {
      setConfigurationError(cause instanceof Error ? cause.message : '无法同步请求配置。')
    }
  }

  /** 保存配置只合并参数和样例，不提交基本信息草稿。 */
  async function saveConfiguration(): Promise<void> {
    if (saving) return
    try {
      setConfigurationError('')
      const patch = buildOperationConfiguration(configuration)
      const saved = await saveForCurrentAddress((latest) => ({ ...latest, ...patch }))
      setConfiguration(operationDraftFromSource(saved))
    } catch (cause) {
      setConfigurationError(cause instanceof Error ? cause.message : '接口配置保存失败。')
    }
  }

  const tabs = [
    {
      key: 'basic', label: '基本信息', className: 'source-detail-content source-operation-scroll',
      children: <OperationBasicInfo saved={savedBasic} draft={basicDraft} saving={saving} error={basicError}
        pendingAddress={pendingImport && debugImportAddress(pendingImport)}
        onEdit={openBasic} onChange={setBasicDraft} onCancel={cancelBasic} onSave={saveBasic} />
    },
    {
      key: 'edit', label: '编辑接口',
      children: <OperationConfigurationPane draft={configuration} theme={theme} saving={saving} error={configurationError}
        activeTab={configurationTab} onTabChange={setConfigurationTab} onChange={setConfiguration}
        onSave={() => void saveConfiguration()} onSyncToDebug={allowDebug ? syncToDebug : undefined} />
    },
    ...(allowDebug ? [{
      key: 'debug', label: '调试接口',
      children: debugMounted && <ExternalApiDebug workspace={workspaceRoot} source={source} operation={operation}
        active={activeTab === 'debug'} seed={debugSeed} onDraftChanged={setDebugChanged} onSyncToEdit={syncToEdit} />
    }] : [])
  ]
  return <>
    <header className="source-drawer-header source-drawer-operation-header source-operation-workspace-header">
      <span className="source-operation-header-icon"><ApiOutlined /><small>API</small></span>
      <div><h3 title={operation.name}>{operation.name}</h3></div>
      <div className="source-drawer-header-actions">
        <Button className="source-operation-header-close" type="text" icon={<CloseOutlined />} aria-label="关闭详情" onClick={onClose} />
      </div>
    </header>
    <Tabs className="source-operation-tabs" size="small" animated={false} destroyInactiveTabPane={false}
      activeKey={activeTab === 'debug' && !allowDebug ? 'basic' : activeTab} onChange={switchTab} items={tabs} />
  </>
}

/** 对比已保存调试草稿，只在已有请求参数会受影响时提示替换。 */
function hasStoredDebugChanges(workspace: string, source: ExternalApiDataSource, operation: DataSourceOperation): boolean {
  const initial = initialDraft(source, operation)
  const stored = restoreDebug(debugStorageKey(workspace, source, operation), initial)
  return !sameDebugDraft(publicDraft(stored.draft), publicDraft(initial))
}
