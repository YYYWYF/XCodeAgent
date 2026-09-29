import type { ReactElement } from 'react'
import {
  iterationOriginLabel,
  iterationOriginOf,
  type DesignOriginTone,
  type IterationOrigin
} from '../../service/iterationOrigin'
import { cx } from '../../utils'
import './IterationBadge.less'

type Props = {
  /** 产物的归属分支名（= 版本号）。 */
  artifactBranch?: string | null
  /** 当前迭代的分支名。 */
  currentBranch?: string | null
  /**
   * 覆盖文案。设计稿专用标注（「v1.0 该设计未设计」）走这里，
   * 因为它的语义与"已完成归属"不同。
   */
  label?: string
  /** 覆盖归属；传了就不再按分支名算（设计稿的"该设计未设计"用它）。 */
  origin?: IterationOrigin
  /**
   * 覆盖视觉档位。只改配色、不改归属语义 —— UI 设计稿的「已设计过」要显著强于
   * 旧迭代的中性档，但归属仍然是"上一轮做的"，所以不能靠改 origin 来实现。
   */
  tone?: DesignOriginTone
}

/**
 * 产物归属徽章：「当前版本」/「v1.0 已完成」。
 *
 * 四个呈现点（快捷任务卡、右侧大纲树、产物详情区、UI 设计稿面板）共用它，
 * 避免各写一套导致同一产物在不同位置标注不一致。
 *
 * 归属未知（老工作区、读不到分支名）时**不渲染** —— 宁可不标，也不要误标成旧迭代。
 */
export default function IterationBadge({
  artifactBranch,
  currentBranch,
  label,
  origin: explicitOrigin,
  tone
}: Props): ReactElement | null {
  const origin = explicitOrigin ?? iterationOriginOf(artifactBranch, currentBranch)
  const text = label ?? iterationOriginLabel(origin, artifactBranch)
  if (!text) return null
  // tone 优先：它表达的是视觉档位，与归属是两个维度。
  const toneClass = tone ?? origin

  return (
    <span
      className={cx('iteration-badge', `is-${toneClass}`)}
      data-origin={origin}
      title={text}
    >
      {text}
    </span>
  )
}
