import { DownOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import { useEffect, useId, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { splitBuildTaskInstructions } from './buildTaskInstructionRows'
import type { BuildTaskInstruction } from './buildTaskInstructionRows'
import './BuildTaskInstructions.less'

/** 展示每条编号说明，长内容默认单行收起，各行可独立展开。 */
export default function BuildTaskInstructions({
  description
}: {
  description: string
}): ReactElement {
  return (
    <ul className={cx('workflow-build-instructions')} aria-label="任务执行说明">
      {splitBuildTaskInstructions(description).map((instruction, index) => (
        <BuildTaskInstructionRow
          instruction={instruction}
          key={`${index}:${instruction.content}`}
        />
      ))}
    </ul>
  )
}

/** 按实际可用宽度检测溢出，只为被截断的说明展示展开箭头。 */
function BuildTaskInstructionRow({
  instruction
}: {
  instruction: BuildTaskInstruction
}): ReactElement {
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const measurementRef = useRef<HTMLSpanElement>(null)
  const contentId = useId()

  useEffect(() => {
    const measurement = measurementRef.current
    if (!measurement) return
    /** 使用单行测量副本，确保展开后以及调整窗口宽度时仍可正确判断是否溢出。 */
    const measure = (): void => {
      setOverflowing(measurement.scrollWidth > measurement.clientWidth + 1)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(measurement)
    return () => observer.disconnect()
  }, [instruction.content])

  return (
    <li className={cx('workflow-build-instruction-row', expanded ? 'expanded' : 'collapsed')}>
      {instruction.number !== undefined && (
        <span className={cx('workflow-build-instruction-number')}>{instruction.number}.</span>
      )}
      <span className={cx('workflow-build-instruction-content')}>
        <span
          aria-hidden="true"
          className={cx('workflow-build-instruction-measure')}
          ref={measurementRef}
        >
          {instruction.content}
        </span>
        <span className={cx('workflow-build-instruction-text')} id={contentId}>
          {instruction.content}
        </span>
      </span>
      {overflowing && (
        <Button
          aria-controls={contentId}
          aria-expanded={expanded}
          aria-label={`${expanded ? '收起' : '展开'}${instruction.number === undefined ? '任务说明' : `第 ${instruction.number} 条任务说明`}`}
          className={cx('workflow-build-instruction-toggle')}
          icon={<DownOutlined rotate={expanded ? 180 : 0} />}
          onClick={(event) => {
            event.stopPropagation()
            setExpanded((current) => !current)
          }}
          onKeyDown={(event) => event.stopPropagation()}
          onKeyPress={(event) => event.stopPropagation()}
          size="small"
          type="text"
        />
      )}
    </li>
  )
}
