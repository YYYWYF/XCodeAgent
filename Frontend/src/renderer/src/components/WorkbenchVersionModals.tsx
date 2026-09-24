import {
  CheckCircleFilled,
  CloudUploadOutlined,
  LoadingOutlined,
  PlusOutlined
} from '@ant-design/icons'
import { Input, Modal, Progress, Radio, Steps } from 'antd'
import RichLoading from './AiChatPanel/components/DesignProgress/RichLoading'
import { validateBranchName } from '../service/repositoryBranch'
import type { ApplicationConfig } from '../typings'
import { cx } from '../utils'

type VersionGenerateState = { stepIndex: number } | null

/** 发起新迭代时用户对分支的选择。 */
export type IterationBranchChoice =
  | { mode: 'current' }
  | { mode: 'new'; branchName: string }

type PublishModalProps = {
  /** 当前正在开发的分支名：提交与推送的落点。 */
  branchName: string
  /** 变更说明草稿。 */
  description: string
  onDescriptionChange: (value: string) => void
  /** 仓库地址：提交与推送的远程落点，弹框内回显。 */
  repoUrl?: string
  generating: VersionGenerateState
  onCancel: () => void
  onGenerate: () => void
}

/** 提交推送弹框：先确认变更说明，确认后以三步进度（打包/提交/推送分支）推进。 */
function PublishBranchModal({
  branchName,
  description,
  onDescriptionChange,
  repoUrl,
  generating,
  onCancel,
  onGenerate
}: PublishModalProps): JSX.Element {
  return (
    <Modal
      centered
      className={cx('workbench-publish-modal', 'is-generate')}
      closable={!generating}
      footer={null}
      maskClosable={!generating}
      onCancel={() => {
        if (!generating) onCancel()
      }}
      open
      width={460}
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
                <Steps.Step
                  title={`推送到版本 ${branchName}`}
                  description="同步到远端仓库"
                />
              </Steps>
            </div>
          ) : (
            <>
              <div className={cx('workbench-generate-reminder')}>
                <p className={cx('workbench-generate-reminder-title')}>提交并推送将执行以下操作:</p>
                <ul>
                  <li>打包本次全部改动(页面 / 接口 / 数据源 / 配置)</li>
                  <li>
                    提交到本地仓库并推送到版本 <strong>{branchName}</strong>
                  </li>
                  <li>版本号对应远端仓库中的一个分支名，每个版本各自占一个分支</li>
                  <li>推送后该版本仍可继续开发，再次提交</li>
                </ul>
              </div>
              <div className={cx('workbench-generate-field')}>
                <label className={cx('workbench-generate-field-label')}>
                  <span className={cx('workbench-generate-required')}>*</span>
                  变更说明
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
            disabled={!!generating || !description.trim()}
            onClick={onGenerate}
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

type IterationModalProps = {
  /** 当前正在开发的分支名，作为「在当前分支继续」的落点。 */
  currentBranchName: string;
  /** 应用已有的分支名，用于即时提示"这个名字已经有了"。 */
  existingBranchNames: string[];
  choice: IterationBranchChoice;
  onChoiceChange: (choice: IterationBranchChoice) => void;
  onCancel: () => void;
  onConfirm: () => void;
};

/**
 * 发起新迭代确认弹框：让用户选择在**当前分支继续**，还是**新建一条分支**。
 *
 * 新建分支时立刻推送到远端，因此这里只做本地即时校验（格式 + 是否与应用已有分支重名）；
 * 远端是否已有同名分支由推送结果反馈（远端可能被同事建过，本地看不到）。
 */
function StartIterationModal({
  currentBranchName,
  existingBranchNames,
  choice,
  onChoiceChange,
  onCancel,
  onConfirm
}: IterationModalProps): JSX.Element {
  const newBranchName = choice.mode === 'new' ? choice.branchName : ''
  const invalidReason =
    choice.mode === 'new' ? validateBranchName(newBranchName) : undefined
  const duplicated =
    choice.mode === 'new' && existingBranchNames.includes(newBranchName.trim())
  const canConfirm = choice.mode === 'current' || (!invalidReason && !duplicated)

  return (
    <Modal
      centered
      className={cx('workbench-publish-modal')}
      closable
      footer={null}
      onCancel={onCancel}
      open
      width={460}
    >
      <div className={cx('workbench-publish-modal-inner')}>
        <header className={cx('workbench-publish-modal-header')}>
          <span className={cx('workbench-publish-modal-icon', 'is-iteration')} aria-hidden="true">
            <PlusOutlined />
          </span>
          <span className={cx('workbench-publish-modal-title')}>
            <strong>发起新迭代</strong>
            <small>回到设计阶段，重新走一遍设计与计划</small>
          </span>
        </header>
        <div className={cx('workbench-publish-modal-body')}>
          <p className={cx('workbench-publish-modal-lead')}>
            新迭代会从设计阶段开始，并使用全新的对话记录。请选择这次迭代在哪儿进行：
          </p>
          <Radio.Group
            className={cx('workbench-iteration-branch-choice')}
            onChange={(e) => {
              const mode = e.target.value as 'current' | 'new'
              onChoiceChange(
                mode === 'current' ? { mode: 'current' } : { mode: 'new', branchName: '' }
              )
            }}
            value={choice.mode}
          >
            <Radio value="current">
              在当前版本 <strong>{currentBranchName}</strong> 上继续
            </Radio>
            <Radio value="new">新建一个版本</Radio>
          </Radio.Group>
          {choice.mode === 'new' ? (
            <div className={cx('workbench-generate-field')}>
              <Input
                value={newBranchName}
                onChange={(e) =>
                  onChoiceChange({ mode: 'new', branchName: e.target.value })
                }
                placeholder="例如 v1.2"
                status={invalidReason || duplicated ? 'error' : undefined}
              />
              <div className={cx('workbench-iteration-branch-hint')}>
                {invalidReason
                  ? invalidReason
                  : duplicated
                    ? '这个版本号应用里已经用过了，换一个吧。'
                    : '新版本会立刻创建并推送到远端仓库。'}
              </div>
            </div>
          ) : null}
          <div className={cx('workbench-publish-modal-meta')}>
            <CheckCircleFilled aria-hidden="true" /> 其他版本保持只读，可随时切换查看
          </div>
        </div>
        <footer className={cx('workbench-publish-modal-footer')}>
          <button className={cx('workbench-publish-modal-cancel')} type="button" onClick={onCancel}>
            取消
          </button>
          <button
            className={cx('workbench-publish-modal-confirm')}
            type="button"
            disabled={!canConfirm}
            onClick={onConfirm}
          >
            <PlusOutlined aria-hidden="true" /> 确认发起
          </button>
        </footer>
      </div>
    </Modal>
  )
}

type WorkbenchVersionModalsProps = {
  application: ApplicationConfig
  /** 提交推送弹框：当前分支名（仅在可提交且弹框开启时传入）。 */
  publishBranchName?: string
  /** 提交推送弹框：仓库地址（提交与推送的远程落点）。 */
  publishRepoUrl?: string
  publishDescription: string
  onDescriptionChange: (value: string) => void
  generating: VersionGenerateState
  onCancelPublish: () => void
  onGenerate: () => void
  /** 迭代弹框：是否开启（开启时用当前分支名与已有分支名渲染选择项）。 */
  iterationModalOpen: boolean
  iterationChoice: IterationBranchChoice
  onIterationChoiceChange: (choice: IterationBranchChoice) => void
  onCancelIteration: () => void
  onConfirmIteration: () => void
  /** 分支切换全屏加载的目标名；为空时不显示。 */
  switchingTargetLabel?: string
}

/** 工作台两个弹框（提交并推送 / 发起新迭代）与切换加载层的组合渲染。 */
export default function WorkbenchVersionModals({
  application,
  publishBranchName,
  publishRepoUrl,
  publishDescription,
  onDescriptionChange,
  generating,
  onCancelPublish,
  onGenerate,
  iterationModalOpen,
  iterationChoice,
  onIterationChoiceChange,
  onCancelIteration,
  onConfirmIteration,
  switchingTargetLabel
}: WorkbenchVersionModalsProps): JSX.Element {
  const allBranches = application.branches || []
  const currentBranchName = application.branchName || allBranches.at(-1)?.name || ''

  return (
    <>
      {publishBranchName ? (
        <PublishBranchModal
          branchName={publishBranchName}
          description={publishDescription}
          onDescriptionChange={onDescriptionChange}
          repoUrl={publishRepoUrl}
          generating={generating}
          onCancel={onCancelPublish}
          onGenerate={onGenerate}
        />
      ) : null}

      {iterationModalOpen ? (
        <StartIterationModal
          choice={iterationChoice}
          currentBranchName={currentBranchName}
          existingBranchNames={allBranches.map((branch) => branch.name)}
          onCancel={onCancelIteration}
          onChoiceChange={onIterationChoiceChange}
          onConfirm={onConfirmIteration}
        />
      ) : null}

      {switchingTargetLabel ? (
        <div className={cx('workbench-version-switching-mask')} role="status" aria-live="polite">
          <div className={cx('workbench-version-switching-card')}>
            <RichLoading bare title={`正在加载版本 ${switchingTargetLabel} 的应用资产…`} />
          </div>
        </div>
      ) : null}
    </>
  )
}
