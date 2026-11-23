import { Input } from 'antd'
import { ExclamationCircleFilled, PlusOutlined, EditOutlined, UpOutlined } from '@ant-design/icons'
import { useId, type ReactElement } from 'react'
import type { MappingAssistState } from './useMappingAssist'
import type { MappingAssistIssue } from './mappingAssistReviewModel'
import { MAPPING_REQUIREMENTS_LIMIT } from './mappingAssistModel'

type SupplementInputProps = {
  label: string; value: string; onChange: (value: string) => void; disabled: boolean; requirementsLength: number
}

/** 所有补充说明输入框共用字数限制和右下角的合计计数。 */
export function MappingAssistTextArea({ label, value, onChange, disabled, requirementsLength, placeholder = '填写取值依据或业务处理说明。', maxRows = 5 }: SupplementInputProps & {
  placeholder?: string; maxRows?: number
}): ReactElement {
  const countId = useId()
  const overLimit = requirementsLength > MAPPING_REQUIREMENTS_LIMIT
  return <span className={`mapping-assist-textarea${disabled ? ' is-disabled' : ''}`}>
    <Input.TextArea aria-label={label} aria-describedby={countId} bordered={false} maxLength={MAPPING_REQUIREMENTS_LIMIT}
      value={value} autoSize={{ minRows: 2, maxRows }} disabled={disabled} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    <span id={countId} className={`mapping-assist-textarea-count${overLimit ? ' is-over-limit' : ''}`}>{`${requirementsLength}/${MAPPING_REQUIREMENTS_LIMIT}`}</span>
  </span>
}

/** 以行内按钮展开选填说明，收起后仍可辨认已有输入。 */
function MappingAssistAnswerInput({ title = '补充说明', ...input }: SupplementInputProps & { title?: string }): ReactElement {
  const filled = Boolean(input.value.trim())
  return <details className="mapping-review-answer">
    <summary>
      <span className="mapping-review-answer-closed">{filled ? <EditOutlined aria-hidden="true" /> : <PlusOutlined aria-hidden="true" />}{filled ? `编辑${title}` : title}</span>
      <span className="mapping-review-answer-open">{title}<span className="mapping-assist-secondary">选填</span><UpOutlined aria-hidden="true" /></span>
    </summary>
    <MappingAssistTextArea {...input} />
  </details>
}

/** 生成失败、取消或采纳后保留可见输入，便于用户核对下次生成会用到的说明。 */
export function MappingAssistSavedAnswers({ assist, disabled }: { assist: MappingAssistState; disabled: boolean }): ReactElement | null {
  const answers = Object.entries(assist.answers)
  if (!answers.length && !assist.clarification) return null
  return <details className="mapping-review-saved-answers"><summary>已填写的补充说明（{answers.length + (assist.clarification ? 1 : 0)}）</summary>
    {answers.map(([key, answer]) => <div key={key}><strong>{answer.label}</strong>
      <MappingAssistAnswerInput label={`${answer.label}的补充说明`} value={answer.value} onChange={(value) => assist.setAnswer(key, answer.label, value)}
        disabled={disabled || assist.phase !== 'idle'} requirementsLength={assist.requirementsLength} />
    </div>)}
    {assist.clarification && <MappingAssistAnswerInput title="其他补充说明" label="已填写的其他补充说明" value={assist.clarification}
      onChange={assist.setClarification} disabled={disabled || assist.phase !== 'idle'} requirementsLength={assist.requirementsLength} />}
  </details>
}

/** 同类目标共用一张卡，字段问题逐条展示，说明仍绑定真实字段身份。 */
export default function MappingAssistIssues({ issues, assist, disabled }: {
  issues: MappingAssistIssue[]; assist: MappingAssistState; disabled: boolean
}): ReactElement {
  const groups = new Map<string, MappingAssistIssue[]>()
  for (const issue of issues) {
    const items = groups.get(issue.group)
    if (items) items.push(issue)
    else groups.set(issue.group, [issue])
  }
  const previous = Object.entries(assist.answers).filter(([key]) => !issues.some((issue) => issue.key === key))
  const inputsDisabled = disabled || assist.phase !== 'idle'
  return <div className="mapping-review-issues">
    {issues.length > 0 && <section className="mapping-review-issues-card" aria-label="待明确问题">
      <h4>待明确问题 <span className="mapping-assist-secondary">· {issues.length}</span></h4>
      {[...groups].map(([group, items]) => <section className="mapping-review-question-group" key={group} aria-label={group}>
        <h5>{group}</h5>
        {items.map((issue) => {
          const answerLabel = issue.path ? issue.label : `${issue.label}（${issue.questions.join('；')}）`
          return <div className="mapping-review-question-entry" key={issue.key}>
            {issue.path && <div className="mapping-review-issue-heading"><code className="mapping-review-issue-target">{issue.path}</code>
              {issue.unconfigured && <span className="mapping-review-issue-status"><ExclamationCircleFilled aria-hidden="true" />取值未配置</span>}</div>}
            {issue.questions.length === 1 ? <p>{issue.questions[0]}</p> : <ol>{issue.questions.map((question) => <li key={question}>{question}</li>)}</ol>}
            <MappingAssistAnswerInput label={`${answerLabel}的补充说明`} value={assist.answers[issue.key]?.value || ''}
              onChange={(value) => assist.setAnswer(issue.key, answerLabel, value)} disabled={inputsDisabled} requirementsLength={assist.requirementsLength} />
          </div>
        })}
      </section>)}
    </section>}
    {previous.length > 0 && <details className="mapping-review-previous-answers"><summary>{assist.requirementsChanged ? '其他已填写的补充说明' : '已用于本轮生成的补充说明'}（{previous.length}）</summary>
      {previous.map(([key, answer]) => <div className="mapping-review-previous-answer" key={key}><strong>{answer.label}</strong>
        <MappingAssistAnswerInput label={`${answer.label}的补充说明`} value={answer.value}
          onChange={(value) => assist.setAnswer(key, answer.label, value)} disabled={inputsDisabled} requirementsLength={assist.requirementsLength} />
      </div>)}
    </details>}
    <MappingAssistAnswerInput title="其他补充说明" label="映射待明确问题的其他补充说明" value={assist.clarification}
      onChange={assist.setClarification} disabled={inputsDisabled} requirementsLength={assist.requirementsLength} />
  </div>
}
