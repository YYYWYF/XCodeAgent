import { Input } from 'antd'
import { ExclamationCircleFilled } from '@ant-design/icons'
import type { TextAreaRef } from 'antd/es/input/TextArea'
import { useEffect, useRef, type ReactElement, type Ref } from 'react'
import type { MappingAssistState } from './useMappingAssist'
import type { MappingAssistIssue } from './mappingAssistReviewModel'
import { MAPPING_REQUIREMENTS_LIMIT } from './mappingAssistModel'

/** 共用字段补充输入，生成失败或重新打开面板时也能查看和修订原回答。 */
export function MappingAssistAnswerInput({ target, label, assist, disabled, textareaRef }: {
  target: string; label: string; assist: MappingAssistState; disabled: boolean; textareaRef?: Ref<TextAreaRef>
}): ReactElement {
  return <label>补充规则 <span className="mapping-assist-secondary">（选填）</span>
    <Input.TextArea ref={textareaRef} aria-label={`${label}的补充规则`} maxLength={900} value={assist.answers[target]?.value || ''}
      autoSize={{ minRows: 2, maxRows: 5 }} disabled={disabled || assist.phase !== 'idle'}
      placeholder="填写已明确的取值或业务规则，重新生成后再预览。" onChange={(event) => assist.setAnswer(target, label, event.target.value)} />
  </label>
}

/** 生成失败、取消或应用后保留可见输入，避免隐藏的补充规则进入下次请求。 */
export function MappingAssistSavedAnswers({ assist, disabled }: { assist: MappingAssistState; disabled: boolean }): ReactElement | null {
  const answers = Object.entries(assist.answers)
  if (!answers.length && !assist.clarification) return null
  return <details className="mapping-review-saved-answers"><summary>已填写的补充信息（{answers.length + (assist.clarification ? 1 : 0)}）</summary>
    {answers.map(([key, answer]) => <div key={key}><strong>{answer.label}</strong><MappingAssistAnswerInput target={key} label={answer.label} assist={assist} disabled={disabled} /></div>)}
    {assist.clarification && <label>其他补充说明<Input.TextArea aria-label="已填写的其他补充说明" value={assist.clarification} maxLength={900} autoSize={{ minRows: 2, maxRows: 5 }}
      disabled={disabled || assist.phase !== 'idle'} onChange={(event) => assist.setClarification(event.target.value)} /></label>}
  </details>
}

/** 将回答绑定到真实字段；填写与暂留未配置都不会直接修改生成结果。 */
export default function MappingAssistIssues({ issues, assist, disabled, focusKey }: {
  issues: MappingAssistIssue[]; assist: MappingAssistState; disabled: boolean; focusKey?: string
}): ReactElement {
  const inputs = useRef<Record<string, TextAreaRef | null>>({})
  // 返回字段中的“待明确”入口直接定位对应回答，不要求从问题列表重新寻找。
  useEffect(() => { if (focusKey) inputs.current[focusKey]?.focus() }, [focusKey])
  const answered = Object.entries(assist.answers)
  const previous = answered.filter(([key]) => !issues.some((issue) => issue.key === key))
  const running = assist.phase !== 'idle'
  return <section aria-label="映射待明确问题">
    {issues.length ? issues.map((issue) => <section className="mapping-review-issue" key={issue.key}>
      <div className="mapping-review-issue-heading"><strong className="mapping-review-issue-target">{issue.path ? <code>{issue.path}</code> : issue.label}</strong>
        <span className="mapping-review-issue-status"><ExclamationCircleFilled aria-hidden="true" />{issue.unconfigured ? '取值未配置' : '待明确'}</span></div>
      <p>{issue.questions[0]}</p>
      {issue.questions.length > 1 && <details><summary>其他待明确内容（{issue.questions.length - 1}）</summary><ul>{issue.questions.slice(1).map((question) => <li key={question}>{question}</li>)}</ul></details>}
      <MappingAssistAnswerInput target={issue.key} label={issue.path ? issue.label : `${issue.label}（${issue.questions.join('；')}）`}
        assist={assist} disabled={disabled} textareaRef={(input) => { inputs.current[issue.key] = input }} />
      {issue.unconfigured && <p className="mapping-assist-secondary">可暂留未配置，替换后手动补全。只使用当前单表，不会自动引入其他来源或推测取值。</p>}
    </section>) : <p className="mapping-assist-secondary">当前没有待明确问题，可继续审阅其他区域。</p>}
    {previous.length > 0 && <details className="mapping-review-previous-answers"><summary>{assist.requirementsChanged ? '其他已填写的补充规则' : '已用于本轮生成的补充规则'}（{previous.length}）</summary>
      {previous.map(([key, answer]) => <div className="mapping-review-previous-answer" key={key}><strong>{answer.label}</strong><MappingAssistAnswerInput target={key} label={answer.label} assist={assist} disabled={disabled} /></div>)}
    </details>}
    <details open className="mapping-review-other-answers"><summary>其他补充说明</summary>
      <Input.TextArea aria-label="映射待明确问题的补充说明" maxLength={900} autoSize={{ minRows: 2, maxRows: 5 }} disabled={running || disabled}
        value={assist.clarification} placeholder="填写不属于单个字段的补充说明。" onChange={(event) => assist.setClarification(event.target.value)} />
    </details>
    <p className="mapping-review-answer-budget mapping-assist-secondary">需求与补充合计 {assist.requirementsLength} / {MAPPING_REQUIREMENTS_LIMIT} 字 · 完成后统一预览</p>
  </section>
}
