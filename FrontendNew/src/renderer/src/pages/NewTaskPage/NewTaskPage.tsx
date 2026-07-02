import { AppstoreOutlined, ArrowUpOutlined, UploadOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import { useState } from 'react'
import './NewTaskPage.less'

const MAX_TASK_LENGTH = 5000

function NewTaskPage(): React.JSX.Element {
  const [taskRequirement, setTaskRequirement] = useState('')
  const trimmedTaskRequirement = taskRequirement.trim()

  return (
    <section className="new-task-page">
      <div className="new-task-page__inner">
        <h1 className="new-task-page__title">
          <span>XcodeAgent</span>
          <span>让你的工作更轻松</span>
        </h1>
        <div className="new-task-composer">
          <textarea
            className="new-task-composer__input"
            maxLength={MAX_TASK_LENGTH}
            placeholder="请输入任务要求，支持直接输入Git仓库地址和分支，或通过@选择已有仓库。"
            value={taskRequirement}
            onChange={(event) => setTaskRequirement(event.target.value)}
          />
          <div className="new-task-composer__footer">
            <div className="new-task-composer__tools">
              <Button aria-label="上传附件" icon={<UploadOutlined />} />
              <Button aria-label="选择已有仓库" icon={<AppstoreOutlined />} />
            </div>
            <div className="new-task-composer__actions">
              <Button
                aria-label="提交任务"
                className="new-task-composer__send"
                disabled={!trimmedTaskRequirement}
                icon={<ArrowUpOutlined />}
                shape="circle"
                type="primary"
              />
              <span className="new-task-composer__count">
                {taskRequirement.length}/{MAX_TASK_LENGTH}
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

export default NewTaskPage
