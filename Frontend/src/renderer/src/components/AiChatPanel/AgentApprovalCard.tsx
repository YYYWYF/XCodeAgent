import {
  CheckCircleOutlined,
  CommentOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { Button, Input, Space, Tag, Typography } from 'antd';
import { useState } from 'react';
import type { AgentApprovalRequest, AgentApprovalStatus } from '../../typings';
import { cx } from '../../utils';

const { Text } = Typography;
const { TextArea } = Input;

type Props = {
  approval: AgentApprovalRequest;
  status?: AgentApprovalStatus;
  loading: boolean;
  onApproveAlways: () => void;
  onApproveOnce: () => void;
  onFeedback: (feedback: string) => void;
};

const riskMeta = {
  low: { label: '低风险', color: 'green' },
  medium: { label: '需要确认', color: 'gold' },
  high: { label: '高风险', color: 'red' },
} as const;

export default function AgentApprovalCard({
  approval,
  status = 'pending',
  loading,
  onApproveAlways,
  onApproveOnce,
  onFeedback,
}: Props) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedback, setFeedback] = useState('');
  const meta = riskMeta[approval.risk.level] ?? riskMeta.low;
  const resolved = status !== 'pending';

  const handleFeedback = () => {
    const nextFeedback = feedback.trim();
    if (!nextFeedback || loading || resolved) return;
    onFeedback(nextFeedback);
  };

  return (
    <div className={cx('agent-approval-card', resolved && 'resolved')}>
      <div className={cx('agent-approval-header')}>
        <Space size={8}>
          <SafetyCertificateOutlined />
          <Text strong>{approval.title || '需要审批'}</Text>
          <Tag color={meta.color}>{meta.label}</Tag>
        </Space>
        {resolved && (
          <Text className={cx('agent-approval-status')}>
            <CheckCircleOutlined /> {formatStatus(status)}
          </Text>
        )}
      </div>

      <div className={cx('agent-approval-subject')}>
        <Text type="secondary">请求执行</Text>
        <Text code title={approval.subject}>
          {approval.subject}
        </Text>
      </div>

      {approval.description && <Text>{approval.description}</Text>}

      {approval.risk.reasons.length > 0 && (
        <ul className={cx('agent-approval-reasons')}>
          {approval.risk.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {approval.details && <pre className={cx('agent-approval-details')}>{approval.details}</pre>}

      {status === 'pending' && (
        <div className={cx('agent-approval-actions')}>
          <Button disabled={loading} loading={loading} onClick={onApproveOnce} type="primary">
            同意，仅本次
          </Button>
          <Button disabled={loading} onClick={onApproveAlways}>
            同意，后续相同命令不再询问
          </Button>
          <Button
            disabled={loading}
            icon={<CommentOutlined />}
            onClick={() => setFeedbackOpen((open) => !open)}
          >
            输入其他意见
          </Button>
        </div>
      )}

      {feedbackOpen && status === 'pending' && (
        <div className={cx('agent-approval-feedback')}>
          <TextArea
            autoSize={{ minRows: 2, maxRows: 4 }}
            disabled={loading}
            placeholder="告诉 agent 你希望它怎么调整这次操作..."
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
          />
          <Button
            disabled={!feedback.trim() || loading}
            loading={loading}
            onClick={handleFeedback}
            type="primary"
          >
            发送意见
          </Button>
        </div>
      )}
    </div>
  );
}

function formatStatus(status: AgentApprovalStatus) {
  if (status === 'approved_always') return '已同意，后续相同命令不再询问';
  if (status === 'approved_once') return '已同意，仅本次';
  if (status === 'feedback') return '已发送其他意见';
  return '等待审批';
}
