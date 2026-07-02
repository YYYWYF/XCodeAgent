import { UserOutlined } from '@ant-design/icons'
import { Avatar } from 'antd'
import './TopBar.less'

function TopBar(): React.JSX.Element {
  return (
    <div className="app-header ant-layout-header">
      <div className="header-user">
        <Avatar className="header-avatar" icon={<UserOutlined />} size={30} />
        <span>个人设置</span>
      </div>
    </div>
  )
}

export default TopBar
