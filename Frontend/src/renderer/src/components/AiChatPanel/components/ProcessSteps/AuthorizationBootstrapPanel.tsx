import { authorizationBootstrapTitle } from '../../../../service/authorizationBootstrap'
import {
  CheckCircleOutlined,
  CopyOutlined,
  WarningOutlined
} from '@ant-design/icons'
import { Button, message } from 'antd'
import { BuiltinAuthorizationWorkflowPanel } from './BuiltinAuthorizationTaskCard'
import type { ReactElement, ReactNode } from 'react'
import type { WorkflowAuthorizationBootstrap } from '../../../../service/authorizationBootstrap'
import { cx } from '../../../../utils'
import './AuthorizationBootstrapPanel.less'

/** 展示权限初始化真实结果及工程原始 DDL，复制操作不改变数据库状态。 */
export default function AuthorizationBootstrapPanel({
  result,
  recoveryNotice,
  workspaceRoot,
  onRefreshed,
  continueDisabled = false
}: {
  workspaceRoot?: string
  continueDisabled?: boolean
  onRefreshed?: (result: WorkflowAuthorizationBootstrap) => void
  recoveryNotice?: ReactNode
  result: WorkflowAuthorizationBootstrap
}): ReactElement | null {
  if (result.status === 'skipped') return null
  if (result.executor === 'builtin_saas') return <BuiltinAuthorizationWorkflowPanel result={result}
    workspace={workspaceRoot} disabled={continueDisabled} onRefreshed={onRefreshed} />
  const warning = result.status === 'failed' || result.status === 'manual_required'
  /** 只复制已校验的完整 SQL，并提供明确的复制结果。 */
  async function copyDdl(): Promise<void> {
    if (!result.ddlAvailable || !result.ddl) return
    try {
      await navigator.clipboard.writeText(result.ddl)
      message.success('已复制完整 DDL')
    } catch {
      message.error('复制失败，请手动选择 SQL 复制')
    }
  }
  return (
    <section className={cx('authorization-bootstrap-panel')} aria-label="权限数据库初始化结果">
      <p role={warning ? 'status' : undefined}>
        {warning ? <WarningOutlined /> : <CheckCircleOutlined />}{' '}
        {authorizationBootstrapTitle(result.status, result)}
      </p>
      {warning && (
        <>
          {recoveryNotice || (result.message && <p>{result.message}</p>)}
          <p>
            数据源：{result.datasourceMode || 'unknown'}；
            {result.executionAttempted ? '已尝试执行脚本' : '未执行脚本'}
          </p>
          <p>工程路径：{result.ddlPath}</p>
          {result.ddlAvailable && result.ddl ? (
            <>
              <Button icon={<CopyOutlined />} onClick={copyDdl}>复制完整 DDL</Button>
              <details className={cx('authorization-bootstrap-ddl')}>
                <summary>查看完整 DDL</summary>
                <pre tabIndex={0} aria-label="完整权限 DDL">
                  {result.ddl}
                </pre>
              </details>
            </>
          ) : (
            !result.message && <p>无法读取 DDL，请检查上述工程文件。</p>
          )}
          <p>
            {recoveryNotice ? '权限初始化尚未完成。' : 'Build 已继续。'}完成 DDL
            执行后，仍需检查资源及管理员初始化。
          </p>
        </>
      )}
    </section>
  )
}
