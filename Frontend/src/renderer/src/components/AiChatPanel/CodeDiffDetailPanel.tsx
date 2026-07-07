import { CloseOutlined, CodeOutlined, FileTextOutlined } from '@ant-design/icons';
import { Button, Empty, Tag, Typography } from 'antd';
import { useEffect, useMemo, useRef, type ReactElement } from 'react';
import type { WorkspaceCodeChangeFile, WorkspaceCodeChangeSet } from '../../typings';
import { cx } from '../../utils';

const { Text, Title } = Typography;

type Props = {
  codeChanges: WorkspaceCodeChangeSet;
  selectedPath?: string;
  onClose: () => void;
};

const changeTypeCopy: Record<WorkspaceCodeChangeFile['changeType'], string> = {
  added: '新增',
  modified: '修改',
  deleted: '删除',
};

export default function CodeDiffDetailPanel({
  codeChanges,
  selectedPath,
  onClose,
}: Props): ReactElement {
  const groupedFiles = useMemo(() => groupCodeChanges(codeChanges.files), [codeChanges.files]);
  const fileSectionRefs = useRef(new Map<string, HTMLElement>());

  useEffect(() => {
    if (!selectedPath) return;

    fileSectionRefs.current.get(selectedPath)?.scrollIntoView({ block: 'start' });
  }, [groupedFiles, selectedPath]);

  const bindFileSectionRef = (path: string): ((node: HTMLElement | null) => void) => (node) => {
    if (node) {
      fileSectionRefs.current.set(path, node);
      return;
    }
    fileSectionRefs.current.delete(path);
  };

  return (
    <section className={cx('code-diff-detail-panel')}>
      <header className={cx('code-diff-detail-header')}>
        <div className={cx('code-diff-detail-title')}>
          <Text className={cx('editor-scope-tag')}>
            CODE REVIEW
          </Text>
          <Title level={4}>代码变更详情</Title>
          <Text type="secondary">
            <span className={cx('addition')}>+{codeChanges.summary.additions}</span>
            <span className={cx('deletion')}>-{codeChanges.summary.deletions}</span>
          </Text>
        </div>
        <Button aria-label="关闭代码变更详情" icon={<CloseOutlined />} onClick={onClose} type="text" />
      </header>

      <div className={cx('code-diff-body')}>
        {groupedFiles.length === 0 ? (
          <Empty description="暂无可展示的代码变更" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          groupedFiles.map((file) => (
            <section
              className={cx('code-diff-file-section', file.path === selectedPath && 'selected')}
              key={file.path}
              ref={bindFileSectionRef(file.path)}
            >
              <div className={cx('code-diff-active-file')}>
                <div>
                  <FileTextOutlined />
                  <Text strong>{file.path}</Text>
                  <Tag>{changeTypeCopy[file.changeType]}</Tag>
                </div>
                <Text className={cx('code-diff-file-stats')} type="secondary">
                  <span className={cx('addition')}>+{file.additions}</span>
                  <span className={cx('deletion')}>-{file.deletions}</span>
                </Text>
              </div>
              {file.changes.map((change, index) => (
                <div className={cx('code-diff-block')} key={`${change.id}-${index}`}>
                  {file.changes.length > 1 && (
                    <div className={cx('code-diff-block-title')}>
                      <CodeOutlined />
                      <Text type="secondary">变更 {index + 1}</Text>
                    </div>
                  )}
                  {change.binary ? (
                    <div className={cx('code-diff-empty')}>
                      <Text type="secondary">Binary file change has no textual diff.</Text>
                    </div>
                  ) : change.diff ? (
                    <pre className={cx('code-diff-lines')}>
                      {parseDiffLines(change.diff).map((line, lineIndex) => (
                        <div className={cx('code-diff-line', line.kind)} key={`${lineIndex}-${line.text}`}>
                          <span className={cx('code-diff-line-marker')}>{line.marker}</span>
                          <code>{line.text}</code>
                        </div>
                      ))}
                    </pre>
                  ) : (
                    <div className={cx('code-diff-empty')}>
                      <Text type="secondary">此文件没有文本行级变更。</Text>
                    </div>
                  )}
                  {change.truncated && (
                    <Text className={cx('code-diff-truncated')} type="secondary">
                      Diff 内容过长，已截断。
                    </Text>
                  )}
                </div>
              ))}
            </section>
          ))
        )}
      </div>
    </section>
  );
}

type GroupedChange = {
  path: string;
  additions: number;
  deletions: number;
  changeType: WorkspaceCodeChangeFile['changeType'];
  changes: WorkspaceCodeChangeFile[];
};

type DiffLine = {
  kind: 'meta' | 'hunk' | 'addition' | 'deletion' | 'context';
  marker: string;
  text: string;
};

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

function parseDiffLines(diff: string): DiffLine[] {
  return diff.split('\n').map((line) => {
    if (line.startsWith('@@')) {
      return { kind: 'hunk', marker: '', text: line };
    }
    if (line.startsWith('+++') || line.startsWith('---')) {
      return { kind: 'meta', marker: line.slice(0, 3), text: line.slice(3).trim() };
    }
    if (line.startsWith('+')) {
      return { kind: 'addition', marker: '+', text: line.slice(1) };
    }
    if (line.startsWith('-')) {
      return { kind: 'deletion', marker: '-', text: line.slice(1) };
    }
    return { kind: 'context', marker: line ? ' ' : '', text: line.startsWith(' ') ? line.slice(1) : line };
  });
}
