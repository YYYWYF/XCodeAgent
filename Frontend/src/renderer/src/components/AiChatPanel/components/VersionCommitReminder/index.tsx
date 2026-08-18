import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  GitlabOutlined,
  ReloadOutlined
} from '@ant-design/icons'
import { Alert, Button, Checkbox, Input, Modal, Spin, Typography, message } from 'antd'
import { useEffect, useMemo, useState, type ReactElement } from 'react'
import { commitVersionControl, inspectVersionControl } from '../../../../service/versionControl'
import type {
  VersionControlCommitResult,
  VersionControlSnapshot,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../../../typings'
import { cx } from '../../../../utils'
import './VersionCommitReminder.less'

const { Paragraph, Text } = Typography
const DEFERRED_STORAGE_PREFIX = 'xcodeagent:version-control:deferred:'

type Props = {
  codeChanges: WorkspaceCodeChangeSet
  disabled: boolean
  onReview: () => void
  workflow: WorkflowRunPayload
}

/** 在二次修改完成后展示分级版本提醒，并承载显式 Git 提交流程。 */
export default function VersionCommitReminder({
  codeChanges,
  disabled,
  onReview,
  workflow
}: Props): ReactElement | null {
  const outcome = quickModificationOutcome(workflow)
  const requestedPaths = useMemo(
    () => Array.from(new Set(codeChanges.files.map((file) => file.path))),
    [codeChanges.files]
  )
  const [snapshot, setSnapshot] = useState<VersionControlSnapshot>()
  const [commitResult, setCommitResult] = useState<VersionControlCommitResult>()
  const [selectedPaths, setSelectedPaths] = useState<string[]>([])
  const [commitMessage, setCommitMessage] = useState('fix: 完成本次快速修改')
  const [inspectError, setInspectError] = useState('')
  const [commitError, setCommitError] = useState('')
  const [inspecting, setInspecting] = useState(false)
  const [committing, setCommitting] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  /** 重新读取 Git 事实，并将仍可提交的文件设为默认选择。 */
  const loadSnapshot = async (): Promise<VersionControlSnapshot | undefined> => {
    if (outcome !== 'completed') return undefined
    setInspecting(true)
    setInspectError('')
    try {
      const nextSnapshot = await inspectVersionControl({
        workspaceRoot: codeChanges.workspaceRoot,
        requestedPaths
      })
      setSnapshot(nextSnapshot)
      setSelectedPaths(nextSnapshot.eligiblePaths)
      const deferredFingerprint = readDeferredFingerprint(codeChanges.id)
      setDismissed(deferredFingerprint === nextSnapshot.fingerprint)
      return nextSnapshot
    } catch (error) {
      setInspectError(error instanceof Error ? error.message : '无法读取当前 Git 状态。')
      return undefined
    } finally {
      setInspecting(false)
    }
  }

  // 只为当前最新的成功快速修改读取一次状态；失败结果只展示审阅提醒。
  useEffect(() => {
    let active = true
    if (outcome !== 'completed') return () => undefined
    setInspecting(true)
    setInspectError('')
    void inspectVersionControl({
      workspaceRoot: codeChanges.workspaceRoot,
      requestedPaths
    })
      .then((nextSnapshot) => {
        if (!active) return
        setSnapshot(nextSnapshot)
        setSelectedPaths(nextSnapshot.eligiblePaths)
        const deferredFingerprint = readDeferredFingerprint(codeChanges.id)
        setDismissed(deferredFingerprint === nextSnapshot.fingerprint)
      })
      .catch((error) => {
        if (!active) return
        setInspectError(error instanceof Error ? error.message : '无法读取当前 Git 状态。')
      })
      .finally(() => {
        if (active) setInspecting(false)
      })
    return () => {
      active = false
    }
  }, [codeChanges.id, codeChanges.workspaceRoot, outcome, requestedPaths])

  /** 暂缓当前指纹的提醒，避免同一批代码在本次会话内重复打扰。 */
  const handleDefer = (): void => {
    if (!snapshot) return
    writeDeferredFingerprint(codeChanges.id, snapshot.fingerprint)
    setDismissed(true)
  }

  /** 打开提交审阅弹窗，并在状态缺失时先完成复核。 */
  const handleOpenCommit = async (): Promise<void> => {
    const currentSnapshot = snapshot ?? (await loadSnapshot())
    if (!currentSnapshot) return
    setCommitError('')
    setModalVisible(true)
  }

  /** 提交弹窗内已明确选择的文件，并保留未选择的工作区变更。 */
  const handleCommit = async (): Promise<void> => {
    if (!snapshot || !selectedPaths.length || !commitMessage.trim() || committing) return
    setCommitting(true)
    setCommitError('')
    try {
      const result = await commitVersionControl({
        workspaceRoot: codeChanges.workspaceRoot,
        requestedPaths,
        selectedPaths,
        expectedFingerprint: snapshot.fingerprint,
        message: commitMessage.trim()
      })
      setCommitResult(result)
      setSnapshot(result.snapshot)
      setModalVisible(false)
      message.success(`本次修改已提交：${result.commitSha.slice(0, 8)}`)
    } catch (error) {
      setCommitError(error instanceof Error ? error.message : '提交失败，请重新检查后重试。')
    } finally {
      setCommitting(false)
    }
  }

  if (!outcome || dismissed) return null

  if (outcome === 'failed') {
    return (
      <section className={cx('version-commit-reminder', 'warning')}>
        <span className={cx('version-commit-icon')}>
          <ExclamationCircleOutlined />
        </span>
        <div className={cx('version-commit-copy')}>
          <Text strong>修改尚未通过验证</Text>
          <Text type="secondary">代码已保留，请先审阅并继续修复；当前不建议提交。</Text>
        </div>
        <Button disabled={disabled} onClick={onReview} size="small">
          审阅变更
        </Button>
      </section>
    )
  }

  if (commitResult) {
    return (
      <section className={cx('version-commit-reminder', 'committed')}>
        <span className={cx('version-commit-icon')}>
          <CheckCircleOutlined />
        </span>
        <div className={cx('version-commit-copy')}>
          <Text strong>本次快速修改已形成独立版本</Text>
          <Text type="secondary">
            {commitResult.commitSha.slice(0, 8)} · {commitResult.committedPaths.length} 个文件
            {commitResult.remainingDirty ? '，其它工作区修改仍保留' : ''}
          </Text>
        </div>
      </section>
    )
  }

  if (!inspecting && snapshot && snapshot.eligiblePaths.length === 0) return null

  return (
    <>
      <section className={cx('version-commit-reminder', inspectError && 'warning')}>
        <span className={cx('version-commit-icon')}>
          {inspecting ? (
            <Spin size="small" />
          ) : inspectError ? (
            <ExclamationCircleOutlined />
          ) : (
            <GitlabOutlined />
          )}
        </span>
        <div className={cx('version-commit-copy')}>
          <Text strong>
            {inspecting
              ? '正在核对当前 Git 状态'
              : inspectError
                ? '暂时无法准备提交'
                : '修改已验证，建议形成独立版本'}
          </Text>
          <Text type="secondary">
            {inspectError ||
              `本轮 ${snapshot?.eligiblePaths.length || 0} 个文件可提交，避免与下一次修改混在一起。`}
          </Text>
        </div>
        <div className={cx('version-commit-actions')}>
          {inspectError ? (
            <Button
              disabled={disabled || inspecting}
              icon={<ReloadOutlined />}
              onClick={() => void loadSnapshot()}
              size="small"
            >
              重试
            </Button>
          ) : (
            <>
              <Button
                disabled={disabled || inspecting}
                onClick={handleDefer}
                size="small"
                type="text"
              >
                稍后
              </Button>
              <Button
                disabled={disabled || inspecting || !snapshot?.eligiblePaths.length}
                onClick={() => void handleOpenCommit()}
                size="small"
                type="primary"
              >
                审阅并提交
              </Button>
            </>
          )}
        </div>
      </section>

      <Modal
        cancelButtonProps={{ disabled: committing }}
        cancelText="取消"
        className={cx('version-commit-modal')}
        destroyOnClose
        getContainer={getWorkbenchContainer}
        maskClosable={!committing}
        okButtonProps={{
          disabled:
            disabled ||
            !selectedPaths.length ||
            !commitMessage.trim() ||
            Boolean(snapshot?.hasStagedChanges)
        }}
        okText="确认提交"
        onCancel={() => setModalVisible(false)}
        onOk={() => void handleCommit()}
        title="提交本次快速修改"
        visible={modalVisible}
        confirmLoading={committing}
        width={600}
      >
        <div className={cx('version-commit-modal-body')}>
          <Paragraph type="secondary">
            提交前已重新读取实际 Git 状态。默认只选择本轮快速修改涉及的文件。
          </Paragraph>
          {snapshot?.hasStagedChanges && (
            <Alert
              message="检测到已有暂存内容，请先在外部处理暂存区，避免混入本次提交。"
              showIcon
              type="warning"
            />
          )}
          {snapshot && snapshot.unavailablePaths.length > 0 && (
            <Alert
              message={`${snapshot.unavailablePaths.length} 个历史变更文件当前已无差异，未加入提交。`}
              showIcon
              type="info"
            />
          )}
          {commitError && (
            <Alert
              action={
                <Button onClick={() => void loadSnapshot()} size="small" type="text">
                  重新检查
                </Button>
              }
              message={commitError}
              showIcon
              type="error"
            />
          )}
          <div className={cx('version-commit-repository')}>
            <span>
              <Text type="secondary">分支</Text>
              <Text>{snapshot?.branch || '—'}</Text>
            </span>
            <span>
              <Text type="secondary">当前版本</Text>
              <Text code>{snapshot?.head.slice(0, 8) || '—'}</Text>
            </span>
          </div>
          <div className={cx('version-commit-field')}>
            <Text strong>选择文件</Text>
            <Checkbox.Group
              onChange={(values) => setSelectedPaths(values.map(String))}
              value={selectedPaths}
            >
              <div className={cx('version-commit-file-list')}>
                {snapshot?.files.map((file) => (
                  <Checkbox key={file.path} value={file.path}>
                    <span className={cx('version-commit-file')}>
                      <span title={file.path}>{file.path}</span>
                      <Text type="secondary">{gitStatusLabel(file.status)}</Text>
                    </span>
                  </Checkbox>
                ))}
              </div>
            </Checkbox.Group>
          </div>
          <div className={cx('version-commit-field')}>
            <Text strong>提交信息</Text>
            <Input
              maxLength={200}
              onChange={(event) => setCommitMessage(event.target.value)}
              placeholder="例如：fix: 优化订单详情交互"
              value={commitMessage}
            />
          </div>
        </div>
      </Modal>
    </>
  )
}

/** 仅把用户最终验收后的显式信号视为可提交状态，避免预览启动成功时提前提醒。 */
function quickModificationOutcome(
  workflow: WorkflowRunPayload
): 'completed' | 'failed' | undefined {
  if (workflow.summary.phase !== 'conversation' || workflow.summary.intent !== 'workspace_change') {
    return undefined
  }
  if (
    workflow.summary.status === 'completed' &&
    workflow.events.some(
      (event) => event.type === 'application-revision' && event.status === 'completed'
    )
  ) {
    return 'completed'
  }
  if (workflow.summary.status === 'failed') return 'failed'
  return undefined
}

/** 让 Modal 挂载到工作台内部，以继承当前明暗主题变量。 */
function getWorkbenchContainer(): HTMLElement {
  return document.querySelector<HTMLElement>(`.${cx('workbench-shell')}`) ?? document.body
}

/** 将 porcelain 状态码转换为紧凑的文件状态文案。 */
function gitStatusLabel(status: string): string {
  if (status === '??') return '新增'
  if (status.includes('D')) return '删除'
  if (status.includes('R')) return '重命名'
  return '修改'
}

/** 读取当前变更集上次暂缓提醒时对应的工作区指纹。 */
function readDeferredFingerprint(changeSetId: string): string {
  try {
    return window.sessionStorage.getItem(`${DEFERRED_STORAGE_PREFIX}${changeSetId}`) || ''
  } catch {
    return ''
  }
}

/** 保存暂缓提醒的工作区指纹，使代码变化后可以重新出现。 */
function writeDeferredFingerprint(changeSetId: string, fingerprint: string): void {
  try {
    window.sessionStorage.setItem(`${DEFERRED_STORAGE_PREFIX}${changeSetId}`, fingerprint)
  } catch {
    // 会话存储不可用时仅保留组件内的暂缓状态。
  }
}
