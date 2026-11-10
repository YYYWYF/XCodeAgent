import { FileAddOutlined, FileTextOutlined } from '@ant-design/icons'
import { Collapse, Typography } from 'antd'
import { cx } from '../../../../utils'
import type { TaskFileChange } from './buildTaskPresentation'
import { TaskFileOperationBadge } from './TaskFileOperationBadge'
import { taskDisclosureExpandIcon } from './TaskDisclosureHeader'
import { TaskFileDescription } from './TaskFileDescription'
import './BuildTaskFileList.less'

/** 文件名下始终显示完整路径，仅在展开后展示执行说明。 */
export function BuildTaskFileList({ files }: { files: TaskFileChange[] }): JSX.Element {
  return (
    <Collapse
      bordered={false}
      className={cx('workflow-task-files')}
      expandIconPosition="right"
      expandIcon={taskDisclosureExpandIcon}
      destroyInactivePanel={false}
    >
      {files.map((file) => (
        <Collapse.Panel
          key={file.path}
          header={
            <div className={cx('workflow-task-file-header')}>
              <span className={cx('workflow-task-file-icon')} aria-hidden="true">
                {file.operations.includes('add') ? <FileAddOutlined /> : <FileTextOutlined />}
              </span>
              <div className={cx('workflow-task-file-summary')}>
                <div className={cx('workflow-task-file-heading')}>
                  {file.operations.map((operation) => (
                    <TaskFileOperationBadge key={operation} operation={operation} />
                  ))}
                  <strong>{file.name}</strong>
                </div>
                <code className={cx('workflow-task-file-path')}>{file.path}</code>
              </div>
            </div>
          }
        >
          <div className={cx('workflow-task-file-detail')}>
            {file.descriptions.length > 0 ? (
              file.descriptions.map((description) => (
                <TaskFileDescription key={description} description={description} />
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
