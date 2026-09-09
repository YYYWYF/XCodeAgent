import {
  CheckCircleFilled,
  CloudUploadOutlined,
  HistoryOutlined,
  LoadingOutlined,
  PlusOutlined
} from '@ant-design/icons'
import { Input, Modal, Progress, Steps } from 'antd'
import RichLoading from './AiChatPanel/components/DesignProgress/RichLoading'
import { findVersion } from '../service/applicationVersions'
import type { ApplicationConfig } from '../typings'
import { cx } from '../utils'

type VersionGenerateState = { stepIndex: number } | null

type PublishModalProps = {
  /** 待发布版本的展示标签（如 v1.0）。 */
  versionLabel: string
  /** 版本日志草稿。 */
  description: string
  onDescriptionChange: (value: string) => void
  generating: VersionGenerateState
  onCancel: () => void
  onGenerate: () => void
}

/** 生成版本弹框：先确认版本日志，确认后以三步进度（打包/提交码云/打Tag）模拟发布。 */
function PublishVersionModal({
  versionLabel,
  description,
  onDescriptionChange,
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
            <strong>生成版本 {versionLabel}</strong>
            <small>打包应用资产 · 提交码云 · 打 Tag</small>
          </span>
        </header>
        <div className={cx('workbench-publish-modal-body')}>
          {generating ? (
            <div className={cx('workbench-generate-progress')}>
              <Progress
                percent={Math.round(((generating.stepIndex + 1) / 3) * 100)}
                showInfo={false}
                strokeColor={{ from: '#6b3cf0', to: '#3f6cf5' }}
              />
              <Steps current={generating.stepIndex} direction="vertical" size="small">
                <Steps.Step title="打包应用资产" description="页面 / 接口 / 数据源 / 配置" />
                <Steps.Step title="提交码云" description="创建提交记录" />
                <Steps.Step title={`打 Tag ${versionLabel}`} description="标记版本里程碑" />
              </Steps>
            </div>
          ) : (
            <>
              <div className={cx('workbench-generate-reminder')}>
                <p className={cx('workbench-generate-reminder-title')}>生成版本将执行以下操作:</p>
                <ul>
                  <li>打包本版本全部应用资产(页面 / 接口 / 数据源 / 配置)</li>
                  <li>
                    提交到码云仓库并打上版本 Tag <strong>{versionLabel}</strong>
                  </li>
                  <li>
                    生成后该版本<strong>锁定为只读</strong>,后续改动需「发起新迭代」
                  </li>
                </ul>
              </div>
              <div className={cx('workbench-generate-field')}>
                <label className={cx('workbench-generate-field-label')}>
                  <span className={cx('workbench-generate-required')}>*</span>
                  版本日志
                </label>
                <Input.TextArea
                  value={description}
                  onChange={(e) => onDescriptionChange(e.target.value)}
                  rows={3}
                  maxLength={200}
                  showCount
                  placeholder="请填写当前版本的提交日志"
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
                <LoadingOutlined aria-hidden="true" /> 生成中…
              </>
            ) : (
              <>
                <CloudUploadOutlined aria-hidden="true" /> 确认生成
              </>
            )}
          </button>
        </footer>
      </div>
    </Modal>
  )
}

type IterationModalProps = {
  /** 发起新迭代所基于的当前版本标签。 */
  versionLabel: string
  /** 当前版本 major 号，弹窗据此展示下一版本号。 */
  major: number
  /** 当前版本 minor 号。 */
  minor: number
  onCancel: () => void
  onConfirm: () => void
}

/** 发起新迭代确认弹框：基于当前版本派生下一个小版本并回到需求分析阶段。 */
function StartIterationModal({
  versionLabel,
  major,
  minor,
  onCancel,
  onConfirm
}: IterationModalProps): JSX.Element {
  return (
    <Modal
      centered
      className={cx('workbench-publish-modal')}
      closable
      footer={null}
      onCancel={onCancel}
      open
      width={420}
    >
      <div className={cx('workbench-publish-modal-inner')}>
        <header className={cx('workbench-publish-modal-header')}>
          <span className={cx('workbench-publish-modal-icon', 'is-iteration')} aria-hidden="true">
            <PlusOutlined />
          </span>
          <span className={cx('workbench-publish-modal-title')}>
            <strong>发起新迭代</strong>
            <small>创建新版本并重新进入需求分析阶段</small>
          </span>
        </header>
        <div className={cx('workbench-publish-modal-body')}>
          <p className={cx('workbench-publish-modal-lead')}>
            将基于 <strong className={cx('workbench-publish-modal-version')}>{versionLabel}</strong>{' '}
            创建 v{major}.{minor + 1} 。新版本会从需求分析阶段开始，使用全新的对话记录。
          </p>
          <div className={cx('workbench-publish-modal-meta')}>
            <CheckCircleFilled aria-hidden="true" /> 已生成版本保持锁定，可随时切换查看
          </div>
        </div>
        <footer className={cx('workbench-publish-modal-footer')}>
          <button className={cx('workbench-publish-modal-cancel')} type="button" onClick={onCancel}>
            取消
          </button>
          <button
            className={cx('workbench-publish-modal-confirm')}
            type="button"
            onClick={onConfirm}
          >
            <PlusOutlined aria-hidden="true" /> 确认发起
          </button>
        </footer>
      </div>
    </Modal>
  )
}

type RollbackModalProps = {
  /** 回退目标版本标签（内容来源）。 */
  restoredVersionLabel: string
  /** 回退后生成的新迭代版本标签。 */
  nextVersionLabel: string
  onCancel: () => void
  onConfirm: () => void
}

/** 基于历史版本迭代（回退）确认弹框：以历史版本内容派生新的顺序版本。 */
function RollbackVersionModal({
  restoredVersionLabel,
  nextVersionLabel,
  onCancel,
  onConfirm
}: RollbackModalProps): JSX.Element {
  return (
    <Modal
      centered
      className={cx('workbench-publish-modal')}
      closable
      footer={null}
      onCancel={onCancel}
      open
      width={420}
    >
      <div className={cx('workbench-publish-modal-inner')}>
        <header className={cx('workbench-publish-modal-header')}>
          <span className={cx('workbench-publish-modal-icon', 'is-rollback')} aria-hidden="true">
            <HistoryOutlined />
          </span>
          <span className={cx('workbench-publish-modal-title')}>
            <strong>基于此版本迭代</strong>
            <small>以历史版本为基础生成新迭代版本</small>
          </span>
        </header>
        <div className={cx('workbench-publish-modal-body')}>
          <p className={cx('workbench-publish-modal-lead')}>
            将基于{' '}
            <strong className={cx('workbench-publish-modal-version')}>{restoredVersionLabel}</strong>{' '}
            的内容生成新迭代版本{' '}
            <strong className={cx('workbench-publish-modal-version')}>{nextVersionLabel}</strong>
            ，以该历史版本为基础继续开发。原有版本保持只读、可随时切换查看，不会被覆盖。
          </p>
          <div className={cx('workbench-publish-modal-meta')}>
            <CheckCircleFilled aria-hidden="true" /> 历史版本保持只读，可随时切换查看
          </div>
        </div>
        <footer className={cx('workbench-publish-modal-footer')}>
          <button className={cx('workbench-publish-modal-cancel')} type="button" onClick={onCancel}>
            取消
          </button>
          <button
            className={cx('workbench-publish-modal-confirm')}
            type="button"
            onClick={onConfirm}
          >
            <HistoryOutlined aria-hidden="true" /> 确认迭代
          </button>
        </footer>
      </div>
    </Modal>
  )
}

type WorkbenchVersionModalsProps = {
  application: ApplicationConfig
  /** 发布弹框：待发布版本标签（仅在可发布且弹框开启时传入）。 */
  publishVersionLabel?: string
  publishDescription: string
  onDescriptionChange: (value: string) => void
  generating: VersionGenerateState
  onCancelPublish: () => void
  onGenerate: () => void
  /** 迭代弹框：作为派生基础的当前版本标签。 */
  iterationBaseVersionId?: string
  onCancelIteration: () => void
  onConfirmIteration: () => void
  /** 回退弹框：目标历史版本 id。 */
  rollbackTargetVersionId?: string
  /** 回退弹框：当前版本链头 id，用于计算新版本号。 */
  activeVersionId: string
  onCancelRollback: () => void
  onConfirmRollback: () => void
  /** 版本切换全屏加载的目标标签；为空时不显示。 */
  switchingTargetLabel?: string
}

/** 工作台三个版本弹框（生成版本/发起新迭代/基于此版本迭代）与切换加载层的组合渲染。 */
export default function WorkbenchVersionModals({
  application,
  publishVersionLabel,
  publishDescription,
  onDescriptionChange,
  generating,
  onCancelPublish,
  onGenerate,
  iterationBaseVersionId,
  onCancelIteration,
  onConfirmIteration,
  rollbackTargetVersionId,
  activeVersionId,
  onCancelRollback,
  onConfirmRollback,
  switchingTargetLabel
}: WorkbenchVersionModalsProps): JSX.Element {
  const iterationBase = iterationBaseVersionId
    ? findVersion(application, iterationBaseVersionId)
    : undefined
  const rollbackTarget = rollbackTargetVersionId
    ? findVersion(application, rollbackTargetVersionId)
    : undefined
  const activeHead = findVersion(application, activeVersionId)

  return (
    <>
      {publishVersionLabel ? (
        <PublishVersionModal
          versionLabel={publishVersionLabel}
          description={publishDescription}
          onDescriptionChange={onDescriptionChange}
          generating={generating}
          onCancel={onCancelPublish}
          onGenerate={onGenerate}
        />
      ) : null}

      {iterationBase ? (
        <StartIterationModal
          versionLabel={iterationBase.versionLabel}
          major={iterationBase.major}
          minor={iterationBase.minor}
          onCancel={onCancelIteration}
          onConfirm={onConfirmIteration}
        />
      ) : null}

      {rollbackTarget ? (
        <RollbackVersionModal
          restoredVersionLabel={rollbackTarget.versionLabel}
          nextVersionLabel={
            activeHead ? `v${activeHead.major}.${activeHead.minor + 1}` : '新版本'
          }
          onCancel={onCancelRollback}
          onConfirm={onConfirmRollback}
        />
      ) : null}

      {switchingTargetLabel ? (
        <div className={cx('workbench-version-switching-mask')} role="status" aria-live="polite">
          <div className={cx('workbench-version-switching-card')}>
            <RichLoading bare title={`正在加载 ${switchingTargetLabel} 版本应用资产…`} />
          </div>
        </div>
      ) : null}
    </>
  )
}
