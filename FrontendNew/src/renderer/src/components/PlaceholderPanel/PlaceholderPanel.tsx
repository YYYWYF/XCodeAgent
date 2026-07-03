import { Typography } from 'antd';
import type { PlaceholderProps } from '../../typings';
import { cx } from '../../utils';
import './PlaceholderPanel.less';

const { Text, Title } = Typography;

export default function PlaceholderPanel({ title, description }: PlaceholderProps) {
  return (
    <section className={cx('placeholder-panel')}>
      <Title level={4}>{title}</Title>
      <Text type="secondary">{description}</Text>
    </section>
  );
}
