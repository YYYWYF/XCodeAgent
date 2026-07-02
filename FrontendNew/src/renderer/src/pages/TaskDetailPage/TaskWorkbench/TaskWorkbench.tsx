import { Segmented } from 'antd'
import { useState } from 'react'
import BrowserPreview from '../BrowserPreview/BrowserPreview'
import PlaceholderPane from '../PlaceholderPane/PlaceholderPane'
import './TaskWorkbench.less'

type WorkbenchTab = 'preview' | 'appConfig' | 'dataSource'

const workbenchTabs: Array<{ key: WorkbenchTab; label: string }> = [
  { key: 'preview', label: '预览' },
  { key: 'appConfig', label: '应用配置' },
  { key: 'dataSource', label: '数据源' }
]

const workbenchTabOptions = workbenchTabs.map((tab) => ({
  label: tab.label,
  value: tab.key
}))

function TaskWorkbench(): React.JSX.Element {
  const [activeTab, setActiveTab] = useState<WorkbenchTab>('preview')

  return (
    <section className="task-workbench">
      <header className="task-workbench__tabs">
        <Segmented
          className="task-workbench__segmented"
          options={workbenchTabOptions}
          value={activeTab}
          onChange={(value) => setActiveTab(value as WorkbenchTab)}
        />
      </header>
      <div className="task-workbench__content">
        {activeTab === 'preview' ? <BrowserPreview /> : null}
        {activeTab === 'appConfig' ? <PlaceholderPane title="应用配置" /> : null}
        {activeTab === 'dataSource' ? <PlaceholderPane title="数据源" /> : null}
      </div>
    </section>
  )
}

export default TaskWorkbench
