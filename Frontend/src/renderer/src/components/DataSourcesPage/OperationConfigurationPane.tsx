import { Alert, Button, Tabs } from 'antd'
import type { ReactNode } from 'react'
import type { DataSourceOperationSection } from '../../typings'
import { confirmWorkspaceAction } from '../workspaceDialogs'
import DataSourceParameterEditor from './DataSourceParameterEditor'
import DataSourceOperationBodyEditor from './DataSourceOperationBodyEditor'
import OperationTabLayout from './OperationTabLayout'
import { clearOperationSection, sectionMeta } from './dataSourceOperationConfig'
import type { OperationDraft } from './operationEditorDraft'

export type OperationConfigurationTab = 'params' | 'header' | 'requestBody' | 'responseBody'

type Props = {
  draft: OperationDraft
  theme: 'light' | 'dark'
  saving: boolean
  error: string
  activeTab: OperationConfigurationTab
  onTabChange: (tab: OperationConfigurationTab) => void
  onChange: (draft: OperationDraft) => void
  onSave: () => void
  onSyncToDebug?: () => void
}

/** 通过内部页签直接编辑请求与响应配置，底栏只作用于配置草稿。 */
export default function OperationConfigurationPane({
  draft, theme, saving, error, activeTab, onTabChange, onChange, onSave, onSyncToDebug
}: Props) {
  /** 清除一个配置项前确认，直到点击保存配置才提交。 */
  function clear(section: Exclude<DataSourceOperationSection, 'path'>): void {
    const label = sectionMeta(section).title
    confirmWorkspaceAction({
      title: '删除' + label + '配置？', content: '将清空这部分草稿，保存配置后生效。',
      okText: '删除配置', cancelText: '取消', okButtonProps: { danger: true },
      onOk: () => onChange(clearOperationSection(draft, section))
    })
  }

  /** 保存中禁用当前子页的输入，页签可继续显示对应草稿。 */
  function fields(children: ReactNode): ReactNode {
    return <fieldset className="source-operation-config-fields" disabled={saving}>{children}</fieldset>
  }

  /** 配置为空时仍提供样例输入，有内容时提供清除入口。 */
  const body = (side: 'request' | 'response'): ReactNode => {
    // 即使为空也可直接填写样例，只有已有内容才显示删除入口。
    const filled = Boolean(draft[side === 'request' ? 'requestSampleText' : 'responseSampleText'].trim() ||
      draft[side === 'request' ? 'requestStructure' : 'responseStructure'])
    return fields(<DataSourceOperationBodyEditor compact side={side} operation={draft} theme={theme} onChange={onChange}
      onRemove={filled ? () => clear(side === 'request' ? 'requestBody' : 'responseBody') : undefined} />)
  }

  const items = [
    { key: 'params', label: '请求参数', children: fields(<>
      <DataSourceParameterEditor compact location="path" parameters={draft.pathParameters}
        onChange={(pathParameters) => onChange({ ...draft, pathParameters })} />
      <DataSourceParameterEditor compact location="query" parameters={draft.queryParameters}
        onChange={(queryParameters) => onChange({ ...draft, queryParameters })}
        onRemove={draft.queryParameters.length ? () => clear('query') : undefined} />
    </>) },
    { key: 'header', label: '请求头', children: fields(
      <DataSourceParameterEditor compact location="header" parameters={draft.headers}
        onChange={(headers) => onChange({ ...draft, headers })}
        onRemove={draft.headers.length ? () => clear('header') : undefined} />
    ) },
    { key: 'requestBody', label: sectionMeta('requestBody').title, children: body('request') },
    { key: 'responseBody', label: sectionMeta('responseBody').title, children: body('response') }
  ].map((item) => ({ ...item, className: 'source-detail-content source-operation-scroll' }))

  return <OperationTabLayout feedback={error && <Alert showIcon type="error" message={error} />}
    sync={onSyncToDebug && { label: '同步至调试接口', disabled: saving, onClick: onSyncToDebug }}
    primaryAction={<Button type="primary" loading={saving} onClick={onSave}>保存配置</Button>}>
    <Tabs className="source-operation-config-tabs source-operation-subtabs" size="small" animated={false} destroyInactiveTabPane={false}
      activeKey={activeTab} onChange={(tab) => onTabChange(tab as OperationConfigurationTab)} items={items} />
  </OperationTabLayout>
}
