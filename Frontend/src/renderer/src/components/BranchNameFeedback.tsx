import { LoadingOutlined } from '@ant-design/icons'
import { useState } from 'react'
import type { BranchNameValidation } from '../hooks/useBranchNameValidation'
import { cx } from '../utils'
import './BranchNameFeedback.less'

type Props = {
  validation: BranchNameValidation
  branchName: string
  onUseSuggestion: (name: string) => void
}

/** 三处入口共用冲突来源、完整已有列表及一键推荐，不覆盖用户手动输入。 */
export default function BranchNameFeedback({ validation, branchName, onUseSuggestion }: Props): JSX.Element {
  const [expanded, setExpanded] = useState(false)
  const { checking, error, existingNames, suggestion, retry } = validation
  const visibleNames = expanded ? existingNames : existingNames.slice(0, 5)
  return (
    <div className={cx('branch-name-feedback')} aria-live="polite">
      <div className={cx('branch-name-feedback-status', error && 'is-error')}>
        {checking ? <><LoadingOutlined /> 正在校验码云仓库分支…</> : error || '新分支不会覆盖已有分支。'}
        {!checking && error ? <button type="button" onClick={retry}>重新校验</button> : null}
      </div>
      {existingNames.length > 0 ? (
        <div>
          已存在：<span className={cx('branch-name-feedback-list')}>{visibleNames.join('、')}</span>
          {existingNames.length > 5 ? (
            <button type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
              {expanded ? '收起' : `查看全部（${existingNames.length}）`}
            </button>
          ) : null}
        </div>
      ) : null}
      {suggestion && suggestion !== branchName.trim() ? (
        <div>建议使用：<strong>{suggestion}</strong><button type="button" onClick={() => onUseSuggestion(suggestion)}>使用此分支名</button></div>
      ) : null}
    </div>
  )
}
