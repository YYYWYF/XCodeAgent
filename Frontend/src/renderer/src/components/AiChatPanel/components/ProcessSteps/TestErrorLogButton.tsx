import { CheckOutlined, CopyOutlined } from '@ant-design/icons'
import { Button, message } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { ArtifactReference } from '../../../../service/workflowArtifacts'
import { readWorkflowArtifact } from '../../../../service/workflowArtifacts'
import { cx } from '../../../../utils'

/** 按需读取当次检查的全部日志，全部读取成功后才写入剪贴板。 */
export default function TestErrorLogButton({
  workspaceRoot,
  references
}: {
  workspaceRoot?: string
  references?: ArtifactReference[]
}): ReactElement {
  const [copying, setCopying] = useState(false)
  const [copiedKey, setCopiedKey] = useState<string>()
  const key = JSON.stringify([workspaceRoot, references])
  const copied = key === copiedKey

  /** 保留日志片段的原始顺序和换行，读取或写入失败时可重试。 */
  const copyErrorLog = async (): Promise<void> => {
    if (copying) return
    if (!workspaceRoot || !references?.length) {
      message.error('本次检查没有可读取的完整错误日志。')
      return
    }
    setCopiedKey(undefined)
    setCopying(true)
    try {
      const parts: string[] = []
      for (const reference of references) {
        const part = await readWorkflowArtifact(workspaceRoot, reference)
        if (typeof part !== 'string') throw new Error('错误日志格式无效。')
        parts.push(part)
      }
      const clipboard = window.devAgentStudio?.clipboard
      if (!clipboard) throw new Error('剪贴板不可用。')
      await clipboard.writeText(parts.join(''))
      setCopiedKey(key)
    } catch (error) {
      message.error(
        error instanceof Error ? `复制失败：${error.message}` : '复制错误日志失败，请重试。'
      )
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
        {copied ? '已复制' : '复制错误日志'}
      </Button>
    </div>
  )
}
