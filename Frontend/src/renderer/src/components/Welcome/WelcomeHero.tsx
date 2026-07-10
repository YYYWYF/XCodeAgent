import { ApiOutlined, CodeOutlined, RocketOutlined } from '@ant-design/icons'
import { Space, Tag, Typography } from 'antd'
import { cx } from '../../utils'

const { Paragraph, Text, Title } = Typography

export default function WelcomeHero() {
  return (
    <header className={cx('welcome-hero')}>
      <div>
        <Text className={cx('welcome-kicker')}>LOCAL APP BUILDER</Text>
        <Title>XCodeAgent</Title>
        <Paragraph>
          面向本地项目的写代码助手，从需求规划到前端页面、后端接口和验证命令，围绕同一个工作目录推进。
        </Paragraph>
      </div>
      <Space className={cx('welcome-tags')} wrap>
        <Tag icon={<CodeOutlined />}>写代码</Tag>
        <Tag icon={<ApiOutlined />}>前后端协作</Tag>
        <Tag icon={<RocketOutlined />}>本地验证</Tag>
      </Space>
    </header>
  )
}
