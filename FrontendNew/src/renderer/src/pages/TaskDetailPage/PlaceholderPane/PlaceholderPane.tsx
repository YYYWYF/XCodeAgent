import { Empty } from 'antd'
import './PlaceholderPane.less'

type PlaceholderPaneProps = {
  title: string
}

function PlaceholderPane({ title }: PlaceholderPaneProps): React.JSX.Element {
  return (
    <div className="placeholder-pane">
      <Empty description={`${title}内容占位`} image={Empty.PRESENTED_IMAGE_SIMPLE} />
    </div>
  )
}

export default PlaceholderPane
