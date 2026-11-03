import {
  CheckCircleFilled,
  CloudUploadOutlined,
  DeleteOutlined,
  LoadingOutlined,
  PlusOutlined,
  RollbackOutlined
} from '@ant-design/icons'
import { Input, Modal, Progress, Radio, Steps } from 'antd'
import RichLoading from './AiChatPanel/components/DesignProgress/RichLoading'
import { validateBranchName } from '../service/repositoryBranch'
import type { ApplicationConfig } from '../typings'
import { cx } from '../utils'

type VersionGenerateState = { stepIndex: number } | null

/** 发起新迭代时用户对分支的选择。 */
export type IterationBranchChoice = { mode: 'current' } | { mode: 'new'; branchName: string }

/** 「基于历史版本迭代」流程对工作区未提交改动的处理方式。 */
export type HistoricalIterationDirtyChoice = 'commit' | 'discard'

/** 「基于历史版本迭代」弹框的输入参数。 */
export type HistoricalIterationRequest = {
  /** 起点历史分支名（从这条分支的 HEAD 建出新分支）。 */
  sourceBranchName: string
  /** 用户输入的新分支名。 */
  newBranchName: string
  /** 工作区当前是否有未提交改动；为 true 时要求用户选择 commit / discard。 */
  hasUncommittedChanges: boolean
  /** hasUncommittedChanges 为 true 时用户选定的处理方式。 */
  dirtyChoice?: HistoricalIterationDirtyChoice
}

type PublishModalProps = {
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
function PublishBranchModal({
  branchName,
  description,
  descriptionGenerating = false,
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
  currentBranchName: string
  /** 应用已有的分支名，用于即时提示"这个名字已经有了"。 */
  existingBranchNames: string[]
  choice: IterationBranchChoice
  onChoiceChange: (choice: IterationBranchChoice) => void
  onCancel: () => void
  onConfirm: () => void
}

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
  const invalidReason = choice.mode === 'new' ? validateBranchName(newBranchName) : undefined
  const duplicated = choice.mode === 'new' && existingBranchNames.includes(newBranchName.trim())
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
                onChange={(e) => onChoiceChange({ mode: 'new', branchName: e.target.value })}
                placeholder="例如 v1_1"
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

type HistoricalIterationModalProps = {
  /** 起点历史分支名（新分支将从这条分支的 HEAD 建出）。 */
  sourceBranchName: string
  /** 当前可编辑（活动）分支名：「审阅并提交」时切回这条分支用。 */
  activeBranchName: string
  /** 应用已有的分支名，用于即时提示"这个名字已经有了"。 */
  existingBranchNames: string[]
  /** 工作区当前是否有未提交改动（决定是否要求选 commit / discard）。 */
  hasUncommittedChanges: boolean
  /** 工作区未提交改动数（提示用）。 */
  uncommittedCount: number
  /** 新分支名草稿。 */
  newBranchName: string
  onNewBranchNameChange: (value: string) => void
  /** 未提交改动处理方式。 */
  dirtyChoice: HistoricalIterationDirtyChoice
  onDirtyChoiceChange: (value: HistoricalIterationDirtyChoice) => void
  onCancel: () => void
  /** 选 discard 或没有未提交改动时的确认（直接进入建分支流程）。 */
  onConfirm: () => void
  /** 选 commit 时的引导：切回当前可编辑分支并打开提交推送弹框。 */
  onGoToCommit: () => void
}

/**
 * 「基于历史版本迭代」确认弹框：以历史分支为起点新建一条分支继续开发。
 *
 * 与「发起新迭代」（从当前 HEAD 出发）的区别：
 * - 起点是历史分支的 HEAD（回滚到那个时刻的代码），不是当前 HEAD。
 * - 工作区必须干净：有未提交改动时强制用户二选一 ——
 *   - 「审阅并提交」：关闭本弹框，切回当前可编辑分支并打开提交推送弹框，
 *     等用户提交完后再回来重新发起（此时 dirty 已为 0）。
 *   - 「丢掉」：直接调后端把未提交改动重置掉，立即进入建分支流程。
 */
function HistoricalIterationModal({
  sourceBranchName,
  activeBranchName,
  existingBranchNames,
  hasUncommittedChanges,
  uncommittedCount,
  newBranchName,
  onNewBranchNameChange,
  dirtyChoice,
  onDirtyChoiceChange,
  onCancel,
  onConfirm,
  onGoToCommit
}: HistoricalIterationModalProps): JSX.Element {
  const invalidReason = validateBranchName(newBranchName)
  const trimmedName = newBranchName.trim()
  const duplicated = trimmedName.length > 0 && existingBranchNames.includes(trimmedName)
  const sameAsSource = trimmedName.length > 0 && trimmedName === sourceBranchName
  const nameReady = !invalidReason && !duplicated && !sameAsSource && trimmedName.length > 0
  // 选 commit 时确认按钮的语义变成「去提交」，不要求分支名（提交完回来再填）；
  // 选 discard 或没有未提交改动时才校验分支名。
  const goingToCommit = hasUncommittedChanges && dirtyChoice === 'commit'
  const canConfirm = goingToCommit ? true : nameReady

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
            <RollbackOutlined />
          </span>
          <span className={cx('workbench-publish-modal-title')}>
            <strong>基于此版本迭代</strong>
            <small>从 {sourceBranchName} 创建新分支继续开发</small>
          </span>
        </header>
        <div className={cx('workbench-publish-modal-body')}>
          <p className={cx('workbench-publish-modal-lead')}>
            将以版本 <strong>{sourceBranchName}</strong> 的代码为起点，新建一条分支继续开发。
            原版本的代码与历史保持不变，仍可随时切回查看。
          </p>
          {hasUncommittedChanges ? (
            <div className={cx('workbench-generate-field')}>
              <label className={cx('workbench-generate-field-label')}>
                <span className={cx('workbench-generate-required')}>*</span>
                当前版本还有 {uncommittedCount} 个未提交改动，如何处理？
              </label>
              <Radio.Group
                className={cx('workbench-iteration-branch-choice')}
                onChange={(e) => onDirtyChoiceChange(e.target.value as HistoricalIterationDirtyChoice)}
                value={dirtyChoice}
              >
                <Radio value="commit">
                  <strong>审阅并提交</strong> — 先回到 {activeBranchName} 提交这些改动，
                  再回来开新分支
                </Radio>
                <Radio value="discard">
                  <DeleteOutlined aria-hidden="true" /> <strong>丢掉</strong> — 放弃这些改动，
                  直接以 {sourceBranchName} 的代码继续
                </Radio>
              </Radio.Group>
              <div className={cx('workbench-iteration-branch-hint')}>
                {dirtyChoice === 'discard'
                  ? '未提交改动会被永久丢弃，不可恢复。'
                  : '会切回当前版本并打开提交推送弹框，提交完后回到这里继续。'}
              </div>
            </div>
          ) : null}
          {!goingToCommit ? (
            <div className={cx('workbench-generate-field')}>
              <label className={cx('workbench-generate-field-label')}>
                <span className={cx('workbench-generate-required')}>*</span>
                新版本号
              </label>
              <Input
                value={newBranchName}
                onChange={(e) => onNewBranchNameChange(e.target.value)}
                placeholder="例如 v1_1"
                status={invalidReason || duplicated || sameAsSource ? 'error' : undefined}
              />
              <div className={cx('workbench-iteration-branch-hint')}>
                {invalidReason
                  ? invalidReason
                  : duplicated
                    ? '这个版本号应用里已经用过了，换一个吧。'
                    : sameAsSource
                      ? '新版本号不能与起点版本相同。'
                      : '新版本会立刻创建并推送到远端仓库。'}
              </div>
            </div>
          ) : null}
          <div className={cx('workbench-publish-modal-meta')}>
            <CheckCircleFilled aria-hidden="true" /> 历史版本保持只读，新分支与历史互不干扰
          </div>
        </div>
        <footer className={cx('workbench-publish-modal-footer')}>
          <button className={cx('workbench-publish-modal-cancel')} type="button" onClick={onCancel}>
            取消
          </button>
          {goingToCommit ? (
            <button
              className={cx('workbench-publish-modal-confirm')}
              type="button"
              onClick={onGoToCommit}
            >
              <CloudUploadOutlined aria-hidden="true" /> 去提交
            </button>
          ) : (
            <button
              className={cx('workbench-publish-modal-confirm')}
              type="button"
              disabled={!canConfirm}
              onClick={onConfirm}
            >
              <RollbackOutlined aria-hidden="true" /> 确认创建并迭代
            </button>
          )}
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
  /** 是否正在按本次改动生成变更说明。 */
  descriptionGenerating?: boolean
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
  /** 「基于此版本迭代」弹框：开启时传入起点分支名。 */
  historicalIterationSourceBranch?: string
  /** 「基于此版本迭代」弹框：新分支名草稿。 */
  historicalIterationBranchName: string
  onHistoricalIterationBranchNameChange: (value: string) => void
  /** 「基于此版本迭代」弹框：当前未提交改动数（> 0 时强制用户选 commit/discard）。 */
  historicalIterationUncommittedCount: number
  /** 「基于此版本迭代」弹框：未提交改动处理方式。 */
  historicalIterationDirtyChoice: HistoricalIterationDirtyChoice
  onHistoricalIterationDirtyChoiceChange: (value: HistoricalIterationDirtyChoice) => void
  onCancelHistoricalIteration: () => void
  /** 选 discard 或无未提交改动时的确认（直接走建分支流程）。 */
  onConfirmHistoricalIteration: () => void
  /** 选 commit 时的引导：切回当前可编辑分支并打开提交推送弹框。 */
  onGoToCommitHistoricalIteration: () => void
  /** 分支切换全屏加载的目标名；为空时不显示。 */
  switchingTargetLabel?: string
}

/** 工作台两个弹框（提交并推送 / 发起新迭代）与切换加载层的组合渲染。 */
export default function WorkbenchVersionModals({
  application,
  publishBranchName,
  publishRepoUrl,
  publishDescription,
  descriptionGenerating,
  onDescriptionChange,
  generating,
  onCancelPublish,
  onGenerate,
  iterationModalOpen,
  iterationChoice,
  onIterationChoiceChange,
  onCancelIteration,
  onConfirmIteration,
  historicalIterationSourceBranch,
  historicalIterationBranchName,
  onHistoricalIterationBranchNameChange,
  historicalIterationUncommittedCount,
  historicalIterationDirtyChoice,
  onHistoricalIterationDirtyChoiceChange,
  onCancelHistoricalIteration,
  onConfirmHistoricalIteration,
  onGoToCommitHistoricalIteration,
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
          descriptionGenerating={descriptionGenerating}
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

      {historicalIterationSourceBranch ? (
        <HistoricalIterationModal
          sourceBranchName={historicalIterationSourceBranch}
          activeBranchName={currentBranchName}
          existingBranchNames={allBranches.map((branch) => branch.name)}
          hasUncommittedChanges={historicalIterationUncommittedCount > 0}
          uncommittedCount={historicalIterationUncommittedCount}
          newBranchName={historicalIterationBranchName}
          onNewBranchNameChange={onHistoricalIterationBranchNameChange}
          dirtyChoice={historicalIterationDirtyChoice}
          onDirtyChoiceChange={onHistoricalIterationDirtyChoiceChange}
          onCancel={onCancelHistoricalIteration}
          onConfirm={onConfirmHistoricalIteration}
          onGoToCommit={onGoToCommitHistoricalIteration}
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
