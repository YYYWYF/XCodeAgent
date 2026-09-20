import { Alert, Button, Checkbox, Input, Modal, Typography } from 'antd'
import { useMemo, type ReactElement } from 'react'
import { cx } from '../../../../utils'
import type { UseMilestoneCommitReturn } from './useMilestoneCommit'
import { areAllSelected, toggleSelectAll } from './fileSelection'

const { Paragraph, Text } = Typography

type Props = {
  title: string
  disabled: boolean
  commit: UseMilestoneCommitReturn
}

/** 里程碑提交审阅弹窗：纯展示组件，状态由 useMilestoneCommit 提供。
 *  从 MilestoneCommitReminder 抽出，供模板就绪卡片与验收提醒复用。 */
export default function MilestoneCommitModal({ title, disabled, commit }: Props): ReactElement {
  const {
    snapshot,
    selectedPaths,
    commitMessage,
    commitError,
    committing,
    modalVisible,
    setCommitMessage,
    setSelectedPaths,
    setModalVisible,
    loadSnapshot,
    handleCommit
  } = commit

  // 批量勾选以弹窗里实际列出的文件为全集。
  const allPaths = useMemo(() => snapshot?.files.map((file) => file.path) ?? [], [snapshot])
  const allSelected = areAllSelected({ allPaths, selectedPaths })

  return (
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
      title={title}
      visible={modalVisible}
      confirmLoading={committing}
      width={600}
    >
      <div className={cx('version-commit-modal-body')}>
        <Paragraph type="secondary">
          提交前已重新读取实际 Git 状态。默认选择全部变更文件，可按需调整。
        </Paragraph>
        {snapshot?.hasStagedChanges && (
          <Alert
            message="检测到已有暂存内容，请先在外部处理暂存区，避免混入本次提交。"
            showIcon
            type="warning"
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
            <Text type="secondary">提交 ID</Text>
            <Text code>{snapshot?.head.slice(0, 8) || '—'}</Text>
          </span>
        </div>
        <div className={cx('version-commit-field')}>
          <div className={cx('version-commit-field-head')}>
            <Text strong>选择文件</Text>
            <span className={cx('version-commit-field-tools')}>
              <Text type="secondary">
                已选 {selectedPaths.length} / {allPaths.length}
              </Text>
              <Button
                disabled={disabled || committing || allPaths.length === 0}
                onClick={() => setSelectedPaths(toggleSelectAll({ allPaths, selectedPaths }))}
                size="small"
                type="link"
              >
                {allSelected ? '取消全选' : '全选'}
              </Button>
            </span>
          </div>
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
            placeholder="例如：feat: 完成页面开发"
            value={commitMessage}
          />
        </div>
      </div>
    </Modal>
  )
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
