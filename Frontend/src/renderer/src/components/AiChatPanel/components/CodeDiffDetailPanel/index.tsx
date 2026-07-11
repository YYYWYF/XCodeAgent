import { CloseOutlined, CodeOutlined, FileTextOutlined } from '@ant-design/icons';
import { Button, Empty, Tag, Typography } from 'antd';
import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import css from 'highlight.js/lib/languages/css';
import java from 'highlight.js/lib/languages/java';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import python from 'highlight.js/lib/languages/python';
import sql from 'highlight.js/lib/languages/sql';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';
import { useEffect, useMemo, useRef, type ReactElement, type ReactNode } from 'react';
import { Diff, parseDiff, type FileData, type GutterOptions } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import type { WorkspaceCodeChangeFile, WorkspaceCodeChangeSet } from '../../../../typings';
import { cx } from '../../../../utils';
import './CodeDiffDetailPanel.less';

const { Text, Title } = Typography;

hljs.registerLanguage('bash', bash);
hljs.registerLanguage('css', css);
hljs.registerLanguage('java', java);
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('json', json);
hljs.registerLanguage('python', python);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('xml', xml);

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
  const selectedFile = groupedFiles.find((file) => file.path === selectedPath) ?? groupedFiles[0];
  const parsedChanges = useMemo(
    () => selectedFile?.changes.map((change) => parseChangeDiff(change)) ?? [],
    [selectedFile],
  );

  return (
    <section className={cx('code-diff-detail-panel')}>
      <header className={cx('code-diff-detail-header')}>
        <div className={cx('code-diff-detail-title')}>
          <Text className={cx('editor-scope-tag')}>
            CODE REVIEW
          </Text>
          <Title level={4}>代码变更详情</Title>
          <Text type="secondary">
            {selectedFile ? (
              <>
                <span className={cx('addition')}>+{selectedFile.additions}</span>
                <span className={cx('deletion')}>-{selectedFile.deletions}</span>
              </>
            ) : (
              '暂无可展示的代码变更'
            )}
          </Text>
        </div>
        <Button
          aria-label="关闭代码变更详情"
          className={cx('code-diff-close-button')}
          icon={<CloseOutlined />}
          onClick={onClose}
          type="text"
        />
      </header>

      <div className={cx('code-diff-body')}>
        {!selectedFile ? (
          <Empty description="暂无可展示的代码变更" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <>
            <div className={cx('code-diff-active-file')}>
              <div>
                <FileTextOutlined />
                <Text strong>{selectedFile.path}</Text>
                <Tag>{changeTypeCopy[selectedFile.changeType]}</Tag>
              </div>
              <Text className={cx('code-diff-file-stats')} type="secondary">
                <span className={cx('addition')}>+{selectedFile.additions}</span>
                <span className={cx('deletion')}>-{selectedFile.deletions}</span>
              </Text>
            </div>
            {selectedFile.changes.map((change, index) => (
              <div className={cx('code-diff-block')} key={`${change.id}-${index}`}>
                {selectedFile.changes.length > 1 && (
                  <div className={cx('code-diff-block-title')}>
                    <CodeOutlined />
                    <Text type="secondary">变更 {index + 1}</Text>
                  </div>
                )}
                {change.binary ? (
                  <div className={cx('code-diff-empty')}>
                    <Text type="secondary">Binary file change has no textual diff.</Text>
                  </div>
                ) : parsedChanges[index].length > 0 ? (
                  <HighlightedDiff files={parsedChanges[index]} path={selectedFile.path} />
                ) : change.diff ? (
                  <pre className={cx('code-diff-raw')}>{change.diff}</pre>
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
          </>
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

type HighlightedDiffProps = {
  files: FileData[];
  path: string;
};

function HighlightedDiff({ files, path }: HighlightedDiffProps): ReactElement {
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const language = languageFromPath(path);
    rootRef.current?.querySelectorAll<HTMLElement>('.diff-code').forEach((element) => {
      const code = element.textContent ?? '';
      element.innerHTML = language
        ? hljs.highlight(code, { language, ignoreIllegals: true }).value
        : hljs.highlightAuto(code).value;
      element.classList.add('hljs');
    });
  }, [files, path]);

  return (
    <div className={cx('code-diff-view')} ref={rootRef}>
      {files.map((file, fileIndex) => (
        <Diff
          diffType={file.type}
          hunks={file.hunks}
          key={`${file.oldPath}-${file.newPath}-${fileIndex}`}
          renderGutter={renderSingleGutter}
          viewType="unified"
        />
      ))}
    </div>
  );
}

function renderSingleGutter({ change, side }: GutterOptions): ReactNode {
  if (side === 'old') return null;
  if (change.type === 'normal') return change.newLineNumber;
  return change.lineNumber;
}

function parseChangeDiff(change: WorkspaceCodeChangeFile): FileData[] {
  if (!change.diff) return [];
  try {
    return parseDiff(normalizeUnifiedDiff(change));
  } catch {
    return [];
  }
}

function normalizeUnifiedDiff(change: WorkspaceCodeChangeFile): string {
  if (/^(?:diff --git|--- |@@ )/m.test(change.diff)) return change.diff;

  const lines = change.diff.split('\n');
  if (lines.at(-1) === '') lines.pop();
  const oldLines = lines.filter((line) => !line.startsWith('+')).length;
  const newLines = lines.filter((line) => !line.startsWith('-')).length;
  const oldPath = change.changeType === 'added' ? '/dev/null' : `a/${change.path}`;
  const newPath = change.changeType === 'deleted' ? '/dev/null' : `b/${change.path}`;
  const oldStart = oldLines === 0 ? 0 : 1;
  const newStart = newLines === 0 ? 0 : 1;

  return [
    `--- ${oldPath}`,
    `+++ ${newPath}`,
    `@@ -${oldStart},${oldLines} +${newStart},${newLines} @@`,
    ...lines,
    '',
  ].join('\n');
}

function languageFromPath(path: string): string | undefined {
  const extension = path.split('.').pop()?.toLowerCase();
  const languages: Record<string, string> = {
    bash: 'bash',
    css: 'css',
    htm: 'xml',
    html: 'xml',
    java: 'java',
    js: 'javascript',
    json: 'json',
    jsx: 'javascript',
    less: 'css',
    py: 'python',
    sh: 'bash',
    sql: 'sql',
    ts: 'typescript',
    tsx: 'typescript',
    xml: 'xml',
  };
  return extension ? languages[extension] : undefined;
}
