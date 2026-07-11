import {
  CheckCircleOutlined,
  CodeOutlined,
  ExportOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import { Button, Space, Tag, Typography } from 'antd';
import { useMemo } from 'react';
import type { WorkspaceCodeChangeFile, WorkspaceCodeChangeSet } from '../../../../typings';
import { cx } from '../../../../utils';
import './CodeChangeCard.less';

const { Text } = Typography;

type Props = {
  codeChanges: WorkspaceCodeChangeSet;
  loading: boolean;
  onApproveAll: () => void;
  onOpenFile: (path: string) => void;
};

type GroupedChange = {
  path: string;
  additions: number;
  deletions: number;
  changeType: WorkspaceCodeChangeFile['changeType'];
  changes: WorkspaceCodeChangeFile[];
};

const changeTypeCopy: Record<WorkspaceCodeChangeFile['changeType'], string> = {
  added: '新增',
  modified: '修改',
  deleted: '删除',
};

export default function CodeChangeCard({
  codeChanges,
  loading,
  onApproveAll,
  onOpenFile,
}: Props) {
  const groupedChanges = useMemo(() => groupCodeChanges(codeChanges.files), [codeChanges.files]);
  const pending = codeChanges.status === 'pending_approval' && Boolean(codeChanges.approvals?.length);
  const resolved = codeChanges.status === 'applied' || codeChanges.status === 'rejected';

  return (
    <div className={cx('code-change-card', pending && 'pending', resolved && 'resolved')}>
      <div className={cx('code-change-header')}>
        <Space size={10}>
          <span className={cx('code-change-icon')}>
            <CodeOutlined />
          </span>
          <div className={cx('code-change-title')}>
            <Text strong>{pending ? '待审核变更' : '文件改动'}</Text>
            <Text className={cx('code-change-count')}>{codeChanges.summary.files} 个文件已变更</Text>
            <span className={cx('code-change-total')}>
              <span className={cx('addition')}>+{codeChanges.summary.additions}</span>
              <span className={cx('deletion')}>-{codeChanges.summary.deletions}</span>
            </span>
          </div>
        </Space>
        {!pending && groupedChanges.length > 0 && (
          <Button
            icon={<ExportOutlined />}
            onClick={() => onOpenFile(groupedChanges[0].path)}
            size="small"
          >
            查看全部变更
          </Button>
        )}
        {pending && <Tag color="gold">{formatStatus(codeChanges.status)}</Tag>}
      </div>

      <div className={cx('code-change-file-list')}>
        {groupedChanges.map((file) => (
          <button
            className={cx('code-change-file-row')}
            key={file.path}
            onClick={() => onOpenFile(file.path)}
            type="button"
          >
            <span className={cx('code-change-file-name')}>
              <FileTextOutlined />
              <span>{file.path}</span>
              <Tag>{changeTypeCopy[file.changeType]}</Tag>
            </span>
            <span className={cx('code-change-file-stats')}>
              <span className={cx('addition')}>+{file.additions}</span>
              <span className={cx('deletion')}>-{file.deletions}</span>
            </span>
          </button>
        ))}
      </div>

      {pending && (
        <div className={cx('code-change-actions')}>
          <Button disabled={loading} loading={loading} onClick={onApproveAll} type="primary">
            审核通过
          </Button>
        </div>
      )}

      {resolved && <span className={cx('code-change-resolved-mark')}><CheckCircleOutlined /></span>}
    </div>
  );
}

function groupCodeChanges(files: WorkspaceCodeChangeFile[]): GroupedChange[] {
  const grouped = new Map<string, GroupedChange>();
  files.forEach((file) => {
    const current = grouped.get(file.path);
    if (!current) {
      grouped.set(file.path, {
        path: file.path,
        additions: file.additions,
        deletions: file.deletions,
        changeType: file.changeType,
        changes: [file],
      });
      return;
    }
    current.additions += file.additions;
    current.deletions += file.deletions;
    current.changes.push(file);
    if (current.changeType !== 'deleted') {
      current.changeType = file.changeType;
    }
  });
  return Array.from(grouped.values());
}

function formatStatus(status: WorkspaceCodeChangeSet['status']) {
  if (status === 'pending_approval') return '待审核';
  if (status === 'rejected') return '已退回';
  return '已应用';
}
