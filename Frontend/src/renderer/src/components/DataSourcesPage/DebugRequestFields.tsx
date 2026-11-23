import { CloseOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Checkbox, Input, InputNumber, Select, Tabs } from 'antd'
import { sectionMeta } from './dataSourceOperationConfig'
import type { Draft, Row } from './debugDraft'
import { isSensitiveDebugField } from './debugPrivacy'
import { FIELD_TYPE_OPTIONS, PATH_FIELD_TYPES } from './jsonFieldTypes'

export type DebugAuth = { mode: string; identity: string; credential: string }
const pathTypeOptions = FIELD_TYPE_OPTIONS.filter((option) => PATH_FIELD_TYPES.includes(option.value))

/** 显示请求参数的启用、名称、类型和值，敏感名称自动隐藏输入。 */
function Rows({ rows, location, update }: {
  rows: Row[]; location: 'path' | 'query' | 'header'; update: (rows: Row[]) => void
}) {
  const fixed = location === 'path'
  /** 更新当前参数行，不改变已有快照。 */
  const change = (index: number, patch: Partial<Row>): void =>
    update(rows.map((row, i) => i === index ? { ...row, ...patch } : row))
  /** 敏感参数改成普通名称时清空旧值，避免落入普通草稿。 */
  const rename = (index: number, name: string): void => {
    const row = rows[index]
    change(index, { name, value: isSensitiveDebugField(row.name) && !isSensitiveDebugField(name) ? '' : row.value })
  }
  return <fieldset className="debug-parameter-group">
    <legend>{sectionMeta(location).title}</legend>
    {rows.length > 0 && <div className="debug-row debug-row-labels">
      <span>{fixed ? '' : '启用'}</span><span>参数名</span><span>字段类型</span><span>值</span>
      <span />
    </div>}
    {rows.map((row, index) => <div className="debug-row" key={index}>
      {fixed ? <span /> : <Checkbox aria-label="启用参数" checked={row.enabled} onChange={event => change(index, {enabled: event.target.checked})} />}
      <Input aria-label="参数名" value={row.name} disabled={fixed} onChange={event => rename(index, event.target.value)} />
      <Select aria-label={(row.name || '参数') + ' 字段类型'} value={row.type} options={fixed ? pathTypeOptions : FIELD_TYPE_OPTIONS}
        dropdownClassName="source-operation-type-menu" onChange={type => change(index, { type })} />
      <Input aria-label={row.name + ' 参数值'} value={row.value} type={isSensitiveDebugField(row.name) ? 'password' : 'text'}
        onChange={event => change(index, {value: event.target.value, enabled: true})} />
      {fixed ? <span /> : <Button type="text" icon={<CloseOutlined />} aria-label="移除参数"
        onClick={() => update(rows.filter((_row, current) => current !== index))} />}
    </div>)}
    {!rows.length && fixed && <p className="debug-muted">当前路径没有占位符</p>}
    {!fixed && <Button className="debug-add" size="small" type="text" icon={<PlusOutlined />}
      onClick={() => update([...rows, { name: '', value: '', enabled: true, type: 'string' }])}>添加参数</Button>}
  </fieldset>
}

/** 管理参数、请求体、鉴权与超时的编辑面板。 */
export default function DebugRequestFields<M extends string>({ draft, auth, update, updateAuth, allowCookie = false }: {
  draft: Draft<M>; auth: DebugAuth; update: (draft: Draft<M>) => void; updateAuth: (auth: DebugAuth) => void
  allowCookie?: boolean
}) {
  return <Tabs className="debug-request source-operation-subtabs" size="small">
    <Tabs.TabPane tab="请求参数" key="params">
      <Rows location="path" rows={draft.path} update={rows => update({ ...draft, path: rows })}/>
      <Rows location="query" rows={draft.query} update={rows => update({ ...draft, query: rows })}/>
    </Tabs.TabPane>
    <Tabs.TabPane tab="请求头" key="headers">
      <Rows location="header" rows={draft.headers} update={rows => update({ ...draft, headers: rows })}/>
    </Tabs.TabPane>
    <Tabs.TabPane tab={sectionMeta('requestBody').title} key="body">
      <Input.TextArea className="debug-body-input" rows={7} aria-label={sectionMeta('requestBody').title} placeholder="输入请求体"
        value={draft.body} onChange={event => update({ ...draft, body: event.target.value })} />
    </Tabs.TabPane>
    <Tabs.TabPane tab="鉴权" key="auth">
      <div className="debug-auth-grid">
        <Select aria-label="鉴权方式" value={auth.mode} onChange={mode => updateAuth({mode, identity: '', credential: ''})}
          options={['无', 'Bearer Token', 'Basic Auth', 'API Key', ...(allowCookie ? ['Cookie'] : [])].map(value => ({value}))}/>
        {['Basic Auth', 'API Key'].includes(auth.mode) && <Input placeholder={auth.mode === 'Basic Auth' ? '用户名' : 'Header 名称'}
          value={auth.identity} onChange={event => updateAuth({...auth, identity: event.target.value})}/>}
        {auth.mode !== '无' && <Input.Password placeholder={allowCookie && auth.mode === 'Cookie' ? 'Cookie，例如 SESSION=…' : '本次调试凭据'} autoComplete="off"
          value={auth.credential} onChange={event => updateAuth({...auth, credential: event.target.value})}/>}
      </div>
    </Tabs.TabPane>
    <Tabs.TabPane tab="设置" key="settings">
      <label className="debug-settings">超时（毫秒） <InputNumber min={100} max={120000} value={draft.timeout}
        onChange={value => typeof value === 'number' && update({...draft, timeout: value})}/></label>
    </Tabs.TabPane>
  </Tabs>
}
