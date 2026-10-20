import { message } from 'antd'
import type { MouseEvent, ReactElement } from 'react'
import { cx } from '../../../utils'
import { localLighthouseReportPath, openLocalReportFile } from '../../../utils/reportFile'
import DocPanel from './DocPanel'
import './TestReportDocument.less'

type Props = {
  content: string
  error: string
  generating: boolean
  title: string
  workspaceRoot?: string
}

/** 展示用户可读测试报告，并复用 Electron 的现有能力打开受控 Lighthouse 文件。 */
export default function TestReportDocument({ workspaceRoot, ...document }: Props): ReactElement {
  /** 拦截当前工作区性能报告链接，其他 Markdown 链接保持原有行为。 */
  const handleLinkClick = (event: MouseEvent<HTMLDivElement>): void => {
    const target = event.target
    if (!(target instanceof Element)) return
    const anchor = target.closest('a')
    const href = anchor?.getAttribute('href') || ''
    if (!href.startsWith('./.devagentstudio/runtime/tests/frontend_performance/')) return
    event.preventDefault()
    const path = localLighthouseReportPath(workspaceRoot, href)
    if (!path) {
      message.error('报告链接无效或缺少工作区信息')
      return
    }
    void openLocalReportFile(path).catch((error: unknown) => {
      message.error(error instanceof Error ? error.message : '打开完整报告失败')
    })
  }

  return (
    <div className={cx('test-report-document')} onClick={handleLinkClick}>
      <DocPanel {...document} docName="测试报告" />
    </div>
  )
}
