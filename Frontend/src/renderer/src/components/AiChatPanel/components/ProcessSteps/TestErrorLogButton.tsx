import { CheckOutlined, CopyOutlined } from '@ant-design/icons'
import { Button, message } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { ArtifactReference } from '../../../../service/workflowArtifacts'
import { readWorkflowArtifact } from '../../../../service/workflowArtifacts'
import { cx } from '../../../../utils'

/** 复制当次检查的完整日志，只有摘要可用时明确提供摘要复制。 */
export default function TestErrorLogButton({
  workspaceRoot,
  references,
  evidence = ''
}: {
  workspaceRoot?: string
  references?: ArtifactReference[]
  evidence?: string
}): ReactElement | null {
  const [copying, setCopying] = useState(false)
  const [copiedKey, setCopiedKey] = useState<string>()
  const fullLog = workspaceRoot && references?.length ? { workspaceRoot, references } : undefined
  const key = JSON.stringify(fullLog ?? evidence)
  const copied = key === copiedKey
  const contentLabel = fullLog ? '日志' : '摘要'
  if (!fullLog && !evidence.trim()) return null

  /** 保留日志片段的原始顺序和换行，读取或写入失败时可重试。 */
  const copyErrorLog = async (): Promise<void> => {
    if (copying) return
    setCopiedKey(undefined)
    setCopying(true)
    try {
      const clipboard = window.devAgentStudio?.clipboard
      if (!clipboard) throw new Error('剪贴板不可用。')
      let text = evidence
      if (fullLog) {
        const parts: string[] = []
        for (const reference of fullLog.references) {
          const part = await readWorkflowArtifact(fullLog.workspaceRoot, reference)
          if (typeof part !== 'string') throw new Error('错误日志格式无效。')
          parts.push(part)
        }
        text = parts.join('')
      }
      await clipboard.writeText(text)
      setCopiedKey(key)
    } catch (error) {
      message.error(error instanceof Error ? `复制失败：${error.message}` : '复制失败，请重试。')
    } finally {
      setCopying(false)
    }
  }

  return (
    <div className={cx('integration-test-check-footer')}>
      <Button
        className={cx('integration-test-check-report-button')}
        icon={copied ? <CheckOutlined /> : <CopyOutlined />}
        loading={copying}
        onClick={() => void copyErrorLog()}
        size="small"
      >
        {copied ? `已复制${contentLabel}` : `复制错误${contentLabel}`}
      </Button>
    </div>
  )
}
