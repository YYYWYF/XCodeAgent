import { useEffect, useState, type ReactElement } from 'react'
import { useSharedRunContext } from './runtimeContext'

/** 根据正式输入合同提供业务字段，最终合法性由 Python 校验。 */
export function RuntimeInput({ schema }: { schema?: Record<string, unknown> }): ReactElement | null {
  const { setInput } = useSharedRunContext()
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [error, setError] = useState('')
  const encoded = JSON.stringify(schema)
  useEffect(() => { setValues({}); setInput(undefined); setError('') }, [encoded, setInput])
  const properties = schema?.properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties) || !Object.keys(properties).length) return null
  /** 保留字段原始类型，不把字符串 false 转成批准，也不默认填造业务数据。 */
  function change(name: string, type: unknown, raw: string | boolean): void {
    try {
      const value: unknown = type === 'number' || type === 'integer' ? (raw === '' ? undefined : Number(raw))
        : type === 'object' || type === 'array' ? JSON.parse(String(raw)) : raw
      const next = { ...values, [name]: value }
      if (value === undefined) delete next[name]
      setValues(next)
      setInput(next)
      setError('')
    } catch { setError('请输入有效的业务字段值。') }
  }
  return <fieldset className="shared-agent-business">
    <legend>业务输入</legend>
    {Object.entries(properties).map(([name, definition]) => {
      const field = definition && typeof definition === 'object' && !Array.isArray(definition)
        ? definition as Record<string, unknown> : {}
      const label = typeof field.title === 'string' ? field.title : name
      return <label key={name}>{label}{Array.isArray(schema.required) && schema.required.includes(name) ? ' *' : ''}
        {field.type === 'boolean' ? <input aria-label={label} type="checkbox" checked={values[name] === true}
          onChange={(event) => change(name, field.type, event.target.checked)} />
          : field.type === 'object' || field.type === 'array' ? <textarea aria-label={label}
            onBlur={(event) => change(name, field.type, event.target.value)} />
            : <input aria-label={label} type={['number', 'integer'].includes(String(field.type)) ? 'number' : 'text'}
              value={String(values[name] ?? '')} onChange={(event) => change(name, field.type, event.target.value)} />}
      </label>
    })}
    {error && <p role="alert">{error}</p>}
  </fieldset>
}
