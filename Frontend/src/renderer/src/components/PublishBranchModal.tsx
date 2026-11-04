import { CloudUploadOutlined, LoadingOutlined } from '@ant-design/icons'
import { Input, Modal, Progress, Steps } from 'antd'
import { useState } from 'react'
import { cx } from '../utils'
import DatabaseDeliveryPreview from './DatabaseDeliveryPreview'
import './WorkbenchVersionModals.less'

type VersionGenerateState = { stepIndex: number } | null

type PublishModalProps = {
  workspaceRoot: string
  isBuiltinDatabase: boolean
  /** 当前正在开发的分支名：提交与推送的落点。 */
  branchName: string
  /** 变更说明草稿。 */
  description: string
  /** 是否正在按本次改动生成变更说明（生成期间输入框仍可编辑）。 */
  descriptionGenerating?: boolean
  onDescriptionChange: (value: string) => void
  /** 仓库地址：提交与推送的远程落点，弹框内回显。 */
  repoUrl?: string
  generating: VersionGenerateState
  onCancel: () => void
  onGenerate: () => void
}

/** 提交推送弹框：先确认变更说明，确认后以三步进度（打包/提交/推送分支）推进。 */
export default function PublishBranchModal({
  workspaceRoot,
  isBuiltinDatabase,
  branchName,
  description,
  descriptionGenerating = false,
  onDescriptionChange,
  repoUrl,
  generating,
  onCancel,
  onGenerate
}: PublishModalProps): JSX.Element {
  const [databaseDeliveryReady, setDatabaseDeliveryReady] = useState(false)
  const canSubmit =
    !generating && !!description.trim() && (!isBuiltinDatabase || databaseDeliveryReady)
  return (
    <Modal
      centered
      className={cx(
        'workbench-publish-modal',
        'is-generate',
        isBuiltinDatabase && 'has-database-delivery'
      )}
      closable={!generating}
      footer={null}
      maskClosable={!generating}
      onCancel={() => {
        if (!generating) onCancel()
      }}
      open
      width={isBuiltinDatabase ? 680 : 460}
    >
      <div className={cx('workbench-publish-modal-inner')}>
        <header className={cx('workbench-publish-modal-header')}>
          <span className={cx('workbench-publish-modal-icon')} aria-hidden="true">
            <CloudUploadOutlined />
          </span>
          <span className={cx('workbench-publish-modal-title')}>
            <strong>提交并推送</strong>
            <small>提交本次改动 · 推送到版本 {branchName}</small>
          </span>
        </header>
        <div className={cx('workbench-publish-modal-body')}>
          {repoUrl ? (
            <div className={cx('workbench-generate-repo')} title={repoUrl}>
              <CloudUploadOutlined aria-hidden="true" />
              <span className={cx('workbench-generate-repo-url')}>{repoUrl}</span>
            </div>
          ) : null}
          {generating ? (
            <div className={cx('workbench-generate-progress')}>
              <Progress
                percent={Math.round(((generating.stepIndex + 1) / 3) * 100)}
                showInfo={false}
                strokeColor={{ from: '#6b3cf0', to: '#3f6cf5' }}
              />
              <Steps current={generating.stepIndex} direction="vertical" size="small">
                <Steps.Step title="打包本次改动" description="页面 / 接口 / 数据源 / 配置" />
                <Steps.Step title="创建提交" description="写入本地仓库" />
                <Steps.Step title={`推送到版本 ${branchName}`} description="同步到远端仓库" />
              </Steps>
            </div>
          ) : (
            <>
              <div className={cx('workbench-generate-reminder')}>
                <p className={cx('workbench-generate-reminder-title')}>提交并推送将执行以下操作:</p>
                <ul>
                  <li>
                    打包本次
                    <span className={cx('workbench-generate-emphasis')}>全部改动</span>
                    (页面 / 接口 / 数据源 / 配置)
                  </li>
                  <li>
                    提交到本地仓库并推送到版本{' '}
                    <span className={cx('workbench-generate-emphasis')}>{branchName}</span>
                  </li>
                  <li>
                    版本号对应远端仓库中的一个分支名，
                    <span className={cx('workbench-generate-emphasis')}>
                      每个版本各自占一个分支
                    </span>
                  </li>
                  <li>
                    推送后该版本
                    <span className={cx('workbench-generate-emphasis')}>仍可继续开发</span>
                    ，再次提交
                  </li>
                  <li>
                    下方填写的变更说明会作为本次的
                    <span className={cx('workbench-generate-emphasis')}>业务变更记录</span>
                    写入提交历史，后续可在该版本内追溯
                  </li>
                </ul>
              </div>
              {isBuiltinDatabase && (
                <DatabaseDeliveryPreview
                  workspaceRoot={workspaceRoot}
                  branchName={branchName}
                  onReadyChange={setDatabaseDeliveryReady}
                />
              )}
              <div className={cx('workbench-generate-field')}>
                <label className={cx('workbench-generate-field-label')}>
                  <span className={cx('workbench-generate-required')}>*</span>
                  变更说明
                  {/* 生成中给出进行时提示，但不禁用输入框：用户可以先写自己的。 */}
                  {descriptionGenerating ? (
                    <span className={cx('workbench-generate-field-hint')}>
                      <LoadingOutlined aria-hidden="true" /> 正在根据本次改动生成…
                    </span>
                  ) : null}
                </label>
                <Input.TextArea
                  value={description}
                  onChange={(e) => onDescriptionChange(e.target.value)}
                  rows={3}
                  maxLength={200}
                  showCount
                  placeholder="请简要描述本次的主要变更内容，例如：新增问卷填报与提交功能"
                />
              </div>
            </>
          )}
        </div>
        <footer className={cx('workbench-publish-modal-footer')}>
          <button
            className={cx('workbench-publish-modal-cancel')}
            type="button"
            disabled={!!generating}
            onClick={onCancel}
          >
            取消
          </button>
          <button
            className={cx('workbench-publish-modal-confirm')}
            type="button"
            disabled={!canSubmit}
            onClick={() => {
              if (canSubmit) onGenerate()
            }}
          >
            {generating ? (
              <>
                <LoadingOutlined aria-hidden="true" /> 提交中…
              </>
            ) : (
              <>
                <CloudUploadOutlined aria-hidden="true" /> 确认提交
              </>
            )}
          </button>
        </footer>
      </div>
    </Modal>
  )
}
