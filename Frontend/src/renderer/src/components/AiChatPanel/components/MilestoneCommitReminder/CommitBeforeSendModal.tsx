import { Button, Modal, Typography } from 'antd'
import { type ReactElement } from 'react'
import { cx } from '../../../../utils'
import '../VersionCommitReminder/VersionCommitReminder.less'

const { Paragraph } = Typography

type Props = {
  visible: boolean
  /** 当前可提交的文件数，用于正文里的 N。 */
  eligibleCount: number
  /** 选「稍后」：记下本次变更的指纹，然后照常发送。 */
  onDefer: () => void
  /** 选「审阅并提交」：交给提交弹窗接管，提交成功后发送。 */
  onReview: () => void
  /** 关掉弹窗（右上角关闭 / 遮罩 / ESC）：放弃这次发送，草稿留在输入框里。 */
  onCancel: () => void
}

/**
 * 发送前门禁弹窗：产品对话输入框在"有未提交变更"时先拦一下。
 *
 * 它本身**不是**提交弹窗 —— 只问"现在提交还是稍后"，选「审阅并提交」后才由复用的
 * `MilestoneCommitModal` 接管文件清单与提交信息。这样文件列表、勾选、合法性校验
 * 都只有一份实现。
 *
 * 两个按钮都走 footer 自定义，而不是用 antd 默认的取消/确认：默认的取消按钮与
 * "点遮罩/ESC 关闭"共用 onCancel，那样「稍后」和「放弃发送」就分不开了 —— 前者要
 * 照常把消息发出去，后者必须什么都不发。
 */
export default function CommitBeforeSendModal({
  visible,
  eligibleCount,
  onDefer,
  onReview,
  onCancel
}: Props): ReactElement {
  return (
    <Modal
      className={cx('version-commit-modal')}
      footer={
        <>
          <Button onClick={onDefer}>稍后</Button>
          <Button onClick={onReview} type="primary">
            审阅并提交
          </Button>
        </>
      }
      getContainer={getWorkbenchContainer}
      onCancel={onCancel}
      title="有未提交的变更"
      visible={visible}
      width={420}
    >
      <div className={cx('version-commit-modal-body')}>
        <Paragraph type="secondary">
          当前 {eligibleCount} 个文件可提交，建议先保存为版本再继续。
        </Paragraph>
      </div>
    </Modal>
  )
}

/** 让 Modal 挂载到工作台内部，以继承当前明暗主题变量。 */
function getWorkbenchContainer(): HTMLElement {
  return document.querySelector<HTMLElement>(`.${cx('workbench-shell')}`) ?? document.body
}
