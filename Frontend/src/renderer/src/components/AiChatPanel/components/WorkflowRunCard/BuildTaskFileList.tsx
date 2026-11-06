import { Collapse, Tag, Typography } from 'antd'
import { cx } from '../../../../utils'
import type { TaskFileChange } from './buildTaskPresentation'
import './BuildTaskFileList.less'

const operationLabels: Record<string, string> = {
  add: '新增',
  modify: '修改',
  delete: '删除',
  rename: '重命名'
}

/** 文件名下始终显示完整路径，仅在展开后展示执行说明。 */
export function BuildTaskFileList({ files }: { files: TaskFileChange[] }): JSX.Element {
  return (
    <Collapse bordered={false} className={cx('workflow-task-files')} expandIconPosition="right">
      {files.map((file) => (
        <Collapse.Panel
          key={file.path}
          header={
            <div className={cx('workflow-task-file-summary')}>
              <div className={cx('workflow-task-file-heading')}>
                {file.operations.map((operation) => (
                  <Tag className={cx('workflow-task-file-operation')} key={operation}>
                    {operationLabels[operation] || operation}
                  </Tag>
                ))}
                <strong>{file.name}</strong>
              </div>
              <code className={cx('workflow-task-file-path')}>{file.path}</code>
            </div>
          }
        >
          <div className={cx('workflow-task-file-detail')}>
            {file.descriptions.length > 0 ? (
              file.descriptions.map((description) => (
                <Typography.Paragraph key={description}>{description}</Typography.Paragraph>
              ))
            ) : (
              <Typography.Text type="secondary">未提供文件级说明</Typography.Text>
            )}
          </div>
        </Collapse.Panel>
      ))}
    </Collapse>
  )
}
