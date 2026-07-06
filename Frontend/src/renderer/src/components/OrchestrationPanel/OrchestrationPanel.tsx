import {
  CheckCircleOutlined,
  DeploymentUnitOutlined,
  PartitionOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';
import { Alert, Button, Space, Tag, Typography } from 'antd';
import type { DevelopmentOrchestrationPayload } from '../../typings';
import { cx } from '../../utils';
import './OrchestrationPanel.less';

const { Text, Title, Paragraph } = Typography;

type Props = {
  orchestration: DevelopmentOrchestrationPayload;
  confirming?: boolean;
  onConfirm?: (orchestration: DevelopmentOrchestrationPayload) => void;
};

export default function OrchestrationPanel({ orchestration, confirming, onConfirm }: Props) {
  const contract = orchestration.contract || orchestration.plan;
  const taskGraph = orchestration.taskGraph || contract?.taskGraph;
  const tasks = taskGraph?.tasks ?? [];
  const batches = orchestration.executionBatches ?? [];
  const run = orchestration.run;
  const confirmed = orchestration.status === 'executing' || Boolean(orchestration.runId);

  if (!contract) return null;

  return (
    <section className={cx('orchestration-panel')}>
      <header className={cx('orchestration-header')}>
        <div>
          <Text className={cx('editor-scope-tag')}>ORCHESTRATION</Text>
          <Title level={5}>{contract.title}</Title>
        </div>
        <Space wrap>
          <Tag color="blue">{targetTypeLabel(contract.targetType)}</Tag>
          <Tag color={confirmed ? 'green' : 'gold'}>{confirmed ? '已确认' : '待确认'}</Tag>
        </Space>
      </header>

      <Paragraph>{contract.summary || orchestration.message}</Paragraph>

      <div className={cx('orchestration-metrics')}>
        <span>
          <DeploymentUnitOutlined /> {contract.features.length} 个功能切片
        </span>
        <span>
          <PartitionOutlined /> {tasks.length} 个任务
        </span>
        <span>
          <CheckCircleOutlined /> {contract.verificationPlan.commands.length} 条验证命令
        </span>
      </div>

      <PanelList title="范围" values={contract.sdd.spec.scopeIn} />
      <PanelList title="任务批次" values={batches.map((batch) => `#${batch.index} ${batch.mode}: ${batch.tasks.join(', ')}`)} />
      <PanelList title="风险" values={contract.risks} />

      {run?.message && <Alert message={run.message} showIcon type={confirmed ? 'success' : 'info'} />}

      <div className={cx('orchestration-actions')}>
        <Text type="secondary">
          {confirmed
            ? `Run: ${orchestration.runId || run?.runId || '未写入运行产物'}`
            : '确认后才会创建 .xcodeagent 运行产物并进入执行调度。'}
        </Text>
        {!confirmed && onConfirm && (
          <Button
            icon={<PlayCircleOutlined />}
            loading={confirming}
            onClick={() => onConfirm(orchestration)}
            type="primary"
          >
            确认执行计划
          </Button>
        )}
      </div>
    </section>
  );
}

function PanelList({ title, values }: { title: string; values: string[] }) {
  if (!values.length) return null;

  return (
    <div className={cx('orchestration-list')}>
      <Text strong>{title}</Text>
      <ul>
        {values.slice(0, 6).map((value) => (
          <li key={value}>{value}</li>
        ))}
      </ul>
    </div>
  );
}

function targetTypeLabel(value: string) {
  if (value === 'frontend') return '前端';
  if (value === 'backend') return '后端';
  return '全栈';
}
