import { Button, Card, Input, Typography } from 'antd';
import { cx } from '../../utils';
import './GlobalConfigPanel.less';

const { Paragraph, Text, Title } = Typography;

export default function GlobalConfigPanel() {
  return (
    <main className={cx('workbench-pane', 'global-config-panel')}>
      <header className={cx('global-config-header')}>
        <div>
          <Text className={cx('global-config-eyebrow')}>APPLICATION SETTINGS</Text>
          <Title level={2}>应用全局配置</Title>
          <Paragraph>管理会作用于整个应用的基础信息、运行环境和安全策略。</Paragraph>
        </div>
        <Button className={cx('save-config-button')} type="primary">
          保存配置
        </Button>
      </header>

      <div className={cx('global-config-content')}>
        <Card className={cx('config-card', 'app-profile-card')} bordered={false}>
          <div className={cx('config-section-heading')}>
            <div className={cx('config-section-icon', 'app-icon')}>XA</div>
            <div>
              <Title level={4}>应用信息</Title>
              <Paragraph>这些信息会展示在应用入口和管理后台。</Paragraph>
            </div>
          </div>
          <div className={cx('config-field-row')}>
            <label>
              <Text>应用名称</Text>
              <Input defaultValue="XCode Agent" />
            </label>
            <label>
              <Text>应用标识</Text>
              <Input defaultValue="xcode-agent" />
            </label>
          </div>
        </Card>

        <div className={cx('config-placeholder')}>
          <Title level={4}>更多全局配置</Title>
          <Paragraph>
            后续可在这里补充应用主题、运行环境、服务能力、访问安全及其他应用级配置。
          </Paragraph>
        </div>
      </div>
    </main>
  );
}
