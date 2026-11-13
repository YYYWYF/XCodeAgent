import type { ReactElement, ReactNode } from 'react';
import { cx } from '../../utils';
import './MarkdownContent.less';

type MarkdownBlock =
  | { type: 'heading'; depth: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; ordered: boolean; items: MarkdownListItem[] }
  | { type: 'code'; code: string; language: string }
  | { type: 'quote'; text: string }
  | { type: 'table'; rows: string[][] }
  | { type: 'rule' };

type MarkdownListItem = {
  text: string;
  children: MarkdownBlock[];
};

type MarkdownContentProps = {
  content: string;
  className?: string;
};

const FENCE_RE = /^```([A-Za-z0-9_-]+)?\s*$/;
const HEADING_RE = /^(#{1,6})\s+(.+)$/;
const ORDERED_LIST_RE = /^\s*\d+[.)]\s+(.+)$/;
const UNORDERED_LIST_RE = /^\s*[-*+]\s+(.+)$/;
const QUOTE_RE = /^>\s?(.*)$/;
const RULE_RE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const TABLE_DIVIDER_CELL_RE = /^:?-{3,}:?$/;
const INLINE_TOKEN_RE =
  /\[([^\]]+)]\(([^)\s]+)(?:\s+"[^"]*")?\)|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\n]+)\*|_([^_\n]+)_/g;

/** 将 Markdown 内容渲染为只读文档。 */
export default function MarkdownContent({ content, className }: MarkdownContentProps): ReactElement | null {
  const blocks = parseMarkdown(content);

  if (!blocks.length) return null;

  return (
    <div className={[cx('markdown-content'), className].filter(Boolean).join(' ')}>
      {blocks.map((block, index) => renderBlock(block, index))}
    </div>
  );
}

/** 将 Markdown 正文解析成块级内容。 */
function parseMarkdown(content: string): MarkdownBlock[] {
  const lines = content.replace(/\r\n?/g, '\n').split('\n');
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmedLine = line.trim();

    if (!trimmedLine) {
      index += 1;
      continue;
    }

    const fence = line.match(FENCE_RE);
    if (fence) {
      const codeLines: string[] = [];
      index += 1;

      while (index < lines.length && !FENCE_RE.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }

      if (index < lines.length) index += 1;
      blocks.push({ type: 'code', code: codeLines.join('\n'), language: fence[1] ?? '' });
      continue;
    }

    const heading = line.match(HEADING_RE);
    if (heading) {
      blocks.push({
        type: 'heading',
        depth: heading[1].length,
        text: heading[2].replace(/\s+#+\s*$/, ''),
      });
      index += 1;
      continue;
    }

    if (RULE_RE.test(line)) {
      blocks.push({ type: 'rule' });
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const rows = [splitTableRow(lines[index])];
      index += 2;

      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }

      blocks.push({ type: 'table', rows });
      continue;
    }

    const quote = line.match(QUOTE_RE);
    if (quote) {
      const quoteLines: string[] = [];

      while (index < lines.length) {
        const quoteLine = lines[index].match(QUOTE_RE);
        if (!quoteLine) break;
        quoteLines.push(quoteLine[1]);
        index += 1;
      }

      blocks.push({ type: 'quote', text: quoteLines.join('\n') });
      continue;
    }

    const listItem = getListItem(line);
    if (listItem) {
      const parsedList = parseList(lines, index);
      blocks.push(parsedList.block);
      index = parsedList.nextIndex;
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }

    if (paragraphLines.length) {
      blocks.push({ type: 'paragraph', text: paragraphLines.join(' ') });
    } else {
      blocks.push({ type: 'paragraph', text: trimmedLine });
      index += 1;
    }
  }

  return blocks;
}

/** 检查当前位置是否开始新的块级内容。 */
function isBlockStart(lines: string[], index: number): boolean {
  const line = lines[index];
  return (
    FENCE_RE.test(line) ||
    HEADING_RE.test(line) ||
    RULE_RE.test(line) ||
    QUOTE_RE.test(line) ||
    Boolean(getListItem(line)) ||
    isTableStart(lines, index)
  );
}

/** 读取列表项的文本、缩进和编号类型。 */
function getListItem(line: string): { indent: number; ordered: boolean; text: string } | null {
  const indent = line.match(/^\s*/)?.[0].length ?? 0;
  const ordered = line.match(ORDERED_LIST_RE);
  if (ordered) return { indent, ordered: true, text: ordered[1] };

  const unordered = line.match(UNORDERED_LIST_RE);
  if (unordered) return { indent, ordered: false, text: unordered[1] };

  return null;
}

/** 递归解析 Markdown 列表缩进层级，保留菜单树需要的父子结构。 */
function parseList(lines: string[], startIndex: number): {
  block: MarkdownBlock;
  nextIndex: number;
} {
  const firstItem = getListItem(lines[startIndex]);
  if (!firstItem) {
    return {
      block: { type: 'list', ordered: false, items: [] },
      nextIndex: startIndex,
    };
  }

  const baseIndent = firstItem.indent;
  const ordered = firstItem.ordered;
  const items: MarkdownListItem[] = [];
  let index = startIndex;

  while (index < lines.length) {
    const currentItem = getListItem(lines[index]);
    if (!currentItem) break;
    if (currentItem.indent < baseIndent || currentItem.ordered !== ordered) break;

    if (currentItem.indent > baseIndent) {
      if (!items.length) break;
      const nestedList = parseList(lines, index);
      items[items.length - 1].children.push(nestedList.block);
      index = nestedList.nextIndex;
      continue;
    }

    const item: MarkdownListItem = { text: currentItem.text, children: [] };
    items.push(item);
    index += 1;

    while (index < lines.length) {
      const nextLine = lines[index];
      const nextItem = getListItem(nextLine);
      if (nextItem) {
        if (nextItem.indent > baseIndent) {
          const nestedList = parseList(lines, index);
          item.children.push(nestedList.block);
          index = nestedList.nextIndex;
          continue;
        }
        break;
      }

      if (!nextLine.trim()) break;
      if (/^\s+/.test(nextLine) && !isBlockStart(lines, index)) {
        item.text = `${item.text} ${nextLine.trim()}`;
        index += 1;
        continue;
      }
      break;
    }
  }

  return {
    block: { type: 'list', ordered, items },
    nextIndex: index,
  };
}

/** 仅在表头与分隔行的列数一致时识别表格。 */
function isTableStart(lines: string[], index: number): boolean {
  const header = lines[index];
  const divider = lines[index + 1];
  if (!header?.includes('|') || !divider?.includes('|')) return false;

  const cells = splitTableRow(divider);
  return (
    cells.length > 0 &&
    cells.length === splitTableRow(header).length &&
    cells.every((cell) => TABLE_DIVIDER_CELL_RE.test(cell))
  );
}

/** 把转义的竖线留在单元格内，仅用未转义的竖线分列。 */
function splitTableRow(line: string): string[] {
  const row = line.trim();
  const cells: string[] = [];
  let cell = '';
  let offset = row.startsWith('|') ? 1 : 0;

  for (const match of row.matchAll(/\\\||\|/g)) {
    const index = match.index;
    if (index < offset) continue;
    cell += row.slice(offset, index);
    offset = index + match[0].length;
    if (match[0] === '|') {
      cells.push(cell.trim());
      cell = '';
    } else {
      cell += '|';
    }
  }

  if (offset < row.length || !row.endsWith('|') || cell) {
    cells.push((cell + row.slice(offset)).trim());
  }
  return cells;
}

/** 根据块类型生成 Markdown 的语义化元素。 */
function renderBlock(block: MarkdownBlock, index: number | string): ReactElement {
  const key = `markdown-block-${String(index)}`;

  if (block.type === 'heading') {
    return renderHeading(block.depth, block.text, key);
  }

  if (block.type === 'paragraph') {
    return <p key={key}>{renderInline(block.text, key)}</p>;
  }

  if (block.type === 'list') {
    const ListTag = block.ordered ? 'ol' : 'ul';
    return (
      <ListTag key={key}>
        {block.items.map((item, itemIndex) => (
          <li key={`${key}-${itemIndex}`}>
            {renderInline(item.text, `${key}-${itemIndex}`)}
            {item.children.map((child, childIndex) =>
              renderBlock(child, `${key}-${itemIndex}-${childIndex}`),
            )}
          </li>
        ))}
      </ListTag>
    );
  }

  if (block.type === 'code') {
    return (
      <pre data-language={block.language || undefined} key={key}>
        <code>{block.code}</code>
      </pre>
    );
  }

  if (block.type === 'quote') {
    return <blockquote key={key}>{renderInline(block.text, key)}</blockquote>;
  }

  if (block.type === 'table') {
    const [header, ...bodyRows] = block.rows;
    return (
      <div className={cx('markdown-table-scroll')} key={key}>
        <table>
          <thead>
            <tr>
              {header.map((cell, cellIndex) => (
                <th key={`${key}-head-${cellIndex}`}>
                  {renderInline(cell, `${key}-head-${cellIndex}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bodyRows.map((row, rowIndex) => (
              <tr key={`${key}-row-${rowIndex}`}>
                {/* 表头决定列数，避免不规则的正文行撑出额外列。 */}
                {header.map((_, cellIndex) => (
                  <td key={`${key}-cell-${rowIndex}-${cellIndex}`}>
                    {renderInline(row[cellIndex] ?? '', `${key}-cell-${rowIndex}-${cellIndex}`)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return <hr key={key} />;
}

/** 按 Markdown 标题深度使用对应的 HTML 标题。 */
function renderHeading(depth: number, text: string, key: string): ReactElement {
  const children = renderInline(text, key);

  if (depth === 1) return <h1 key={key}>{children}</h1>;
  if (depth === 2) return <h2 key={key}>{children}</h2>;
  if (depth === 3) return <h3 key={key}>{children}</h3>;
  if (depth === 4) return <h4 key={key}>{children}</h4>;
  if (depth === 5) return <h5 key={key}>{children}</h5>;
  return <h6 key={key}>{children}</h6>;
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let offset = 0;

  while (offset < text.length) {
    const codeStart = text.indexOf('`', offset);

    if (codeStart === -1) {
      nodes.push(...renderInlineWithoutCode(text.slice(offset), `${keyPrefix}-${offset}`));
      break;
    }

    if (codeStart > offset) {
      nodes.push(...renderInlineWithoutCode(text.slice(offset, codeStart), `${keyPrefix}-${offset}`));
    }

    const tickCount = countBackticks(text, codeStart);
    const marker = '`'.repeat(tickCount);
    const codeEnd = text.indexOf(marker, codeStart + tickCount);

    if (codeEnd === -1) {
      nodes.push(text.slice(codeStart));
      break;
    }

    nodes.push(
      <code key={`${keyPrefix}-code-${codeStart}`}>
        {text.slice(codeStart + tickCount, codeEnd)}
      </code>,
    );
    offset = codeEnd + tickCount;
  }

  return nodes;
}

function renderInlineWithoutCode(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;

  INLINE_TOKEN_RE.lastIndex = 0;
  for (const match of text.matchAll(INLINE_TOKEN_RE)) {
    const fullMatch = match[0];
    const matchIndex = match.index ?? 0;

    if (matchIndex > lastIndex) {
      nodes.push(text.slice(lastIndex, matchIndex));
    }

    const [linkLabel, href, strongStar, strongUnderscore, emphasisStar, emphasisUnderscore] =
      match.slice(1);
    const key = `${keyPrefix}-${matchIndex}`;

    if (linkLabel && href) {
      const safeHref = getSafeHref(href);
      nodes.push(
        safeHref ? (
          <a href={safeHref} key={key} rel="noreferrer" target="_blank">
            {renderInline(linkLabel, key)}
          </a>
        ) : (
          <span key={key}>{linkLabel}</span>
        ),
      );
    } else if (strongStar || strongUnderscore) {
      nodes.push(<strong key={key}>{renderInline(strongStar || strongUnderscore, key)}</strong>);
    } else if (emphasisStar || emphasisUnderscore) {
      nodes.push(<em key={key}>{renderInline(emphasisStar || emphasisUnderscore, key)}</em>);
    }

    lastIndex = matchIndex + fullMatch.length;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

/** 统计代码标记开头连续的反引号。 */
function countBackticks(text: string, start: number): number {
  let count = 0;
  while (text[start + count] === '`') count += 1;
  return count;
}

/** 仅返回受支持的链接协议或相对路径。 */
function getSafeHref(href: string): string | null {
  const normalizedHref = href.trim();
  if (/^(https?:|mailto:|tel:)/i.test(normalizedHref)) return normalizedHref;
  if (/^(#|\/|\.\/|\.\.\/)/.test(normalizedHref)) return normalizedHref;
  return null;
}
