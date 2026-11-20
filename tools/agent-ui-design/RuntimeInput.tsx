import { useId, useLayoutEffect, useState, type ReactElement } from 'react'
import { useAgentUIState } from '@za38.03/agent-ui-runtime'
import { useSharedRunContext } from './runtimeContext'
import { useRuntimeResult } from './useRuntimeResult'

/** 表单草稿按会话和合同隔离；运行及待确认期间保留原参数，不干预官方恢复。 */
export function RuntimeInput({ schema, agentId }: { schema?: Record<string, unknown>; agentId?: string }): ReactElement | null {
  const runtime = useAgentUIState((snapshot) => snapshot.runtime)
  const result = useRuntimeResult(agentId)
  const threadId = runtime?.conversation.id ?? ''
  const locked = runtime?.run.status === 'running' || Boolean(runtime?.interrupts.length)
    || (result?.threadId === threadId && result.status === 'awaiting_user')
  return <BusinessInputForm key={`${threadId}:${JSON.stringify(schema)}`} schema={schema} locked={locked} />
}

/** 默认仅展示填写入口；退出或卸载时清理表单输入，最终合法性由 Python 校验。 */
function BusinessInputForm({ schema, locked }: { schema?: Record<string, unknown>; locked: boolean }): ReactElement | null {
  const { setInput } = useSharedRunContext()
  const formId = useId()
  const [expanded, setExpanded] = useState(false)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [error, setError] = useState('')
  useLayoutEffect(() => {
    setInput(undefined)
    return (): void => setInput(undefined)
  }, [setInput])
  const properties = schema?.properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties) || !Object.keys(properties).length) return null
  /** 退出填写会明确撤销参数；普通折叠不能留下用户看不到的旧值。 */
  function toggle(): void {
    if (locked) return
    setExpanded(!expanded)
    setValues({})
    setInput(undefined)
    setError('')
  }
  /** 删除字段表示不提供；空字符串仍可作为显式清空备注的合法值。 */
  function update(name: string, value: unknown): void {
    if (locked) return
    const next = { ...values, [name]: value }
    if (value === undefined) delete next[name]
    setValues(next)
    setInput(Object.keys(next).length ? next : undefined)
    setError('')
  }
  /** 保留字段原始类型，不把字符串 false 转成批准，也不默认填造业务数据。 */
  function change(name: string, type: unknown, raw: string | boolean): void {
    try {
      const value: unknown = type === 'number' || type === 'integer' ? (raw === '' ? undefined : Number(raw))
        : type === 'object' || type === 'array' ? (raw === '' ? undefined : JSON.parse(String(raw))) : raw
      update(name, value)
    } catch {
      update(name, undefined)
      setError('请输入有效的业务字段值。')
    }
  }
  return <div className="shared-agent-business">
    <button type="button" aria-expanded={expanded} aria-controls={formId} disabled={locked} onClick={toggle}>
      {expanded ? '退出填写并清空' : '填写业务参数'}</button>
    {expanded && <fieldset id={formId} disabled={locked}>
      <legend>业务参数</legend>
      <p>仅在需要时填写，参数会随消息发送。填写不代表确认执行；普通聊天和查询无需填写修改字段。</p>
      {locked && <p role="status">正在运行或等待处理，请先完成当前操作。</p>}
      {Object.entries(properties).map(([name, definition]) => {
        const field = definition && typeof definition === 'object' && !Array.isArray(definition)
          ? definition as Record<string, unknown> : {}
        const label = typeof field.title === 'string' ? field.title : name
        const options: unknown[] | null = Array.isArray(field.enum) ? field.enum : null
        const descriptionId = `${formId}-${name}`
        const describedBy = typeof field.description === 'string' ? descriptionId : undefined
        return <div className="shared-agent-business-field" key={name}><label>{label}{Array.isArray(schema.required) && schema.required.includes(name) ? ' *' : ''}
          {options ? <select aria-label={label} aria-describedby={describedBy}
            value={Object.hasOwn(values, name) ? String(options.findIndex((value) => value === values[name])) : ''}
            onChange={(event) => update(name, event.target.value === '' ? undefined : options[Number(event.target.value)])}>
            <option value="">不提供</option>
            {options.map((value, index) => <option key={index} value={String(index)}>{String(value)}</option>)}
          </select> : field.type === 'boolean' ? <input aria-label={label} aria-describedby={describedBy} type="checkbox" checked={values[name] === true}
            onChange={(event) => change(name, field.type, event.target.checked)} />
            : field.type === 'object' || field.type === 'array' ? <textarea aria-label={label} aria-describedby={describedBy}
              onBlur={(event) => change(name, field.type, event.target.value)} />
              : <input aria-label={label} aria-describedby={describedBy} type={['number', 'integer'].includes(String(field.type)) ? 'number' : 'text'}
                value={String(values[name] ?? '')} onChange={(event) => change(name, field.type, event.target.value)} />}
        </label>
        {describedBy && <p id={descriptionId}>{String(field.description)}</p>}
        </div>
      })}
      {error && <p role="alert">{error}</p>}
    </fieldset>}
  </div>
}
