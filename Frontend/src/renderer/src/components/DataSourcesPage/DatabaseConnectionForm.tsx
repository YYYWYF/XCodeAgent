import { ApiOutlined, KeyOutlined } from '@ant-design/icons'
import { Alert, Input, InputNumber, Radio } from 'antd'
import { useId } from 'react'
import type { ReactElement, ReactNode } from 'react'
import type { DatabaseSourceMode } from '../../typings'
import { cx } from '../../utils'

export type DatabaseConnectionDraft = {
  name: string; mode: DatabaseSourceMode; domain: string; port: string
  schema: string; userName: string; dbid: string; password: string
}
export type DatabaseConnectionErrors = Partial<Record<keyof DatabaseConnectionDraft, string>>

type Props = {
  value: DatabaseConnectionDraft
  errors: DatabaseConnectionErrors
  disabled: boolean
  hasPassword: boolean
  onChange: (patch: Partial<DatabaseConnectionDraft>) => void
}

/** 分组展示外部数据库连接，只允许选择直连或 DBID，保留已存在内置连接的受管身份。 */
export default function DatabaseConnectionForm({ value, errors, disabled, hasPassword, onChange }: Props): ReactElement {
  const id = useId()

  /** 为字段统一生成关联标签、必填标识及可访问的错误说明。 */
  const field = (name: keyof DatabaseConnectionDraft, label: string, control: ReactNode, required = true, help?: string): ReactElement => (
    <div className={cx('database-connection-field')}>
      <label htmlFor={`${id}-${name}`}>
        {label}{required ? <em aria-hidden="true">*</em> : null}
        {name === 'password' && hasPassword ? <small>已配置</small> : null}
      </label>
      {control}
      {errors[name] ? <span id={`${id}-${name}-error`} className={cx('database-connection-field-error')}>{errors[name]}</span>
        : help ? <span id={`${id}-${name}-help`} className={cx('database-connection-help')}>{help}</span> : null}
    </div>
  )

  /** 为输入控件绑定一致的禁用状态及错误关联。 */
  const inputProps = (name: keyof DatabaseConnectionDraft) => ({
    id: `${id}-${name}`, disabled, status: errors[name] ? 'error' as const : undefined,
    'aria-invalid': Boolean(errors[name]),
    'aria-describedby': errors[name] ? `${id}-${name}-error` : undefined,
  })

  return (
    <div className={cx('database-connection-form')}>
      {field('name', '连接名称', <Input {...inputProps('name')} aria-required onChange={(event) => onChange({ name: event.target.value })} placeholder="例如：业务数据库" value={value.name} />)}
      {value.mode === 'builtin' ? (
        <Alert showIcon type="info" message="当前连接由应用配置管理。" />
      ) : <>
        <div className={cx('database-connection-mode')}>
          <span className={cx('database-connection-label')}>连接方式</span>
          <Radio.Group aria-label="连接方式" disabled={disabled} onChange={(event) => onChange({ mode: event.target.value })} value={value.mode}>
            <Radio value="direct"><span className={cx('database-connection-mode-content')}><ApiOutlined /><span><strong>数据库直连</strong><small>使用数据库地址与账号</small></span></span></Radio>
            <Radio value="dbid"><span className={cx('database-connection-mode-content')}><KeyOutlined /><span><strong>DBID</strong><small>通过 DBID 获取访问凭据</small></span></span></Radio>
          </Radio.Group>
        </div>
        <div className={cx('database-connection-section-title')}>连接信息</div>
        <div className={cx('database-connection-fields')}>
          <div className={cx('database-connection-endpoint')}>
            {field('domain', '数据库地址', <Input {...inputProps('domain')} aria-required autoComplete="off" onChange={(event) => onChange({ domain: event.target.value })} placeholder="主机地址或 IP" value={value.domain} />)}
            {field('port', '端口', <InputNumber {...inputProps('port')} aria-required controls={false} min={1} max={65535} precision={0} onChange={(port) => onChange({ port: port === null ? '' : String(port) })} placeholder="3306" value={value.port ? Number(value.port) : undefined} />)}
          </div>
          <div className={cx('database-connection-pair')}>
            {field('schema', 'Schema', <Input {...inputProps('schema')} aria-required autoComplete="off" onChange={(event) => onChange({ schema: event.target.value })} placeholder="数据库名称" value={value.schema} />)}
            {field('userName', '用户名', <Input {...inputProps('userName')} aria-required autoComplete="off" onChange={(event) => onChange({ userName: event.target.value })} placeholder="数据库用户名" value={value.userName} />)}
          </div>
          {value.mode === 'dbid'
            ? field('dbid', 'DBID', <Input {...inputProps('dbid')} aria-required aria-describedby={errors.dbid ? `${id}-dbid-error` : `${id}-dbid-help`} autoComplete="off" onChange={(event) => onChange({ dbid: event.target.value })} placeholder="输入数据库 DBID" value={value.dbid} />, true, '使用 DBID 获取数据库访问凭据。')
            : field('password', '密码', <Input.Password {...inputProps('password')} aria-required={!hasPassword} aria-describedby={errors.password ? `${id}-password-error` : hasPassword ? `${id}-password-help` : undefined} autoComplete="new-password" onChange={(event) => onChange({ password: event.target.value })} placeholder={hasPassword ? '输入新密码' : '输入数据库密码'} value={value.password} />, !hasPassword, hasPassword ? '留空则保留已保存的密码。' : undefined)}
        </div>
      </>}
    </div>
  )
}
