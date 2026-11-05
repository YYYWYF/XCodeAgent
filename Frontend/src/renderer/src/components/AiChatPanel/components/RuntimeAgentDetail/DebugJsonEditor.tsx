import { Button } from 'antd'
import { useId, useMemo } from 'react'
import CodeMirror, { EditorView } from '@uiw/react-codemirror'
import { json, jsonParseLinter } from '@codemirror/lang-json'
import { linter } from '@codemirror/lint'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags } from '@lezer/highlight'
import { checkDebugJson } from '../../../../service/debugJsonValidation'
import type { DebugJsonOptions } from '../../../../service/debugJsonValidation'
import './DebugJsonEditor.less'

const editorTheme = EditorView.theme({
  '&': { color: 'var(--wb-text)', backgroundColor: 'var(--wb-canvas)', fontSize: '13px' },
  '.cm-content': { caretColor: 'var(--wb-accent)', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', lineHeight: '22px' },
  '.cm-gutters': { color: 'var(--wb-text-tertiary)', backgroundColor: 'var(--wb-surface-subtle)', borderColor: 'var(--wb-border)' },
  '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'var(--wb-surface-subtle)' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'var(--wb-accent-soft)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-tooltip': { color: 'var(--wb-text)', backgroundColor: 'var(--wb-surface)', borderColor: 'var(--wb-border)' }
})
const highlight = syntaxHighlighting(HighlightStyle.define([
  { tag: [tags.string, tags.propertyName], color: 'var(--wb-accent)' },
  { tag: [tags.number, tags.bool, tags.null], color: 'var(--wb-development-completed)' },
  { tag: tags.punctuation, color: 'var(--wb-text-secondary)' }
]))

/** 使用现成 CodeMirror JSON 扩展提供行号、高亮、折叠、缩进和语法诊断，业务层只绑定输入与契约结果。 */
export default function DebugJsonEditor({ label, value, onChange, disabled, schema, objectOnly, validate, compact }: {
  label: string; value: string; onChange: (value: string) => void; disabled?: boolean; compact?: boolean
} & DebugJsonOptions): JSX.Element {
  const statusId = useId()
  const result = checkDebugJson(value, { schema, objectOnly, validate })
  const errors = result.errors
  const extensions = useMemo(() => [json(), linter(jsonParseLinter()), editorTheme, highlight,
    EditorView.contentAttributes.of({ 'aria-label': label, 'aria-invalid': String(errors.length > 0), 'aria-describedby': statusId })],
  [label, errors.length, statusId])
  return <div className={`debug-json-editor${compact ? ' is-compact' : ''}`} data-invalid={errors.length > 0} data-validator-error={Boolean(result.validatorError)}>
    <div className="debug-json-toolbar"><span>{label}</span><div>
      <span className="debug-json-state" data-valid={!errors.length && !result.validatorError}>{result.validatorError ? '校验器不可用' : errors.length ? `${errors.length} 项问题` : schema ? '契约通过' : 'JSON 有效'}</span>
      <Button type="text" size="small" disabled={disabled || !result.syntaxValid}
        onClick={() => onChange(JSON.stringify(result.value, null, 2))}>格式化</Button></div></div>
    <CodeMirror aria-label={label} aria-invalid={errors.length > 0} aria-describedby={statusId} value={value}
      theme="none" height={compact ? '140px' : '240px'} extensions={extensions} editable={!disabled} readOnly={disabled}
      basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true, autocompletion: true }}
      onChange={onChange} />
    <div className="debug-json-feedback" id={statusId} aria-live="polite">
      {errors.length ? <ul>{errors.slice(0, 3).map((error, index) => <li key={index}>{error}</li>)}</ul>
        : !result.validatorError && <span>{schema ? '输入符合当前接口契约' : 'JSON 语法校验通过'}</span>}
      {errors.length > 3 && <span>另有 {errors.length - 3} 项问题</span>}
      {result.validatorError && <p className="debug-json-validator-error" role="alert">{result.validatorError}</p>}
    </div>
  </div>
}
