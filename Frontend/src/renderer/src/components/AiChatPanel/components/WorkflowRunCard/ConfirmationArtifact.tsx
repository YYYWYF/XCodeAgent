import { Typography } from 'antd'
import type { ReactElement } from 'react'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import type { WorkflowConfirmationArtifact } from '../../../../typings'
import { cx } from '../../../../utils'

const { Text } = Typography

type ConfirmationArtifactProps = {
  artifact: WorkflowConfirmationArtifact
}

export default function ConfirmationArtifact({
  artifact
}: ConfirmationArtifactProps): ReactElement {
  const title = artifact.id === 'requirement_spec' ? '需求文档' : '项目计划'

  return (
    <section className={cx('workflow-confirmation-artifact')}>
      <div className={cx('workflow-confirmation-artifact-header')}>
        <Text strong>{title}</Text>
        <Text code>{artifact.name}</Text>
      </div>
      <Text className={cx('workflow-confirmation-artifact-path')} type="secondary">
        {artifact.path}
      </Text>
      <div className={cx('workflow-confirmation-artifact-content')}>
        <MarkdownContent content={artifact.content} />
      </div>
    </section>
  )
}
