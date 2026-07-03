import type { ReactNode } from 'react';
import { cx } from '../../utils';
import './MarkdownContent.less';

type MarkdownBlock =
  | { type: 'heading'; depth: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'code'; code: string; language: string }
  | { type: 'quote'; text: string }
  | { type: 'table'; rows: string[][] }
  | { type: 'rule' };

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

export default function MarkdownContent({ content, className }: MarkdownContentProps) {
  const blocks = parseMarkdown(content);

  if (!blocks.length) return null;

  return (
    <div className={[cx('markdown-content'), className].filter(Boolean).join(' ')}>
      {blocks.map((block, index) => renderBlock(block, index))}
    </div>
  );
}

function parseMarkdown(content: string) {
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
      const items: string[] = [];

      while (index < lines.length) {
        const nextItem = getListItem(lines[index]);
        if (nextItem?.ordered === listItem.ordered) {
          items.push(nextItem.text);
          index += 1;
          continue;
        }

        if (items.length && /^\s{2,}\S/.test(lines[index])) {
          items[items.length - 1] = `${items[items.length - 1]} ${lines[index].trim()}`;
          index += 1;
          continue;
        }

        break;
      }

      blocks.push({ type: 'list', ordered: listItem.ordered, items });
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

function isBlockStart(lines: string[], index: number) {
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

function getListItem(line: string) {
  const ordered = line.match(ORDERED_LIST_RE);
  if (ordered) return { ordered: true, text: ordered[1] };

  const unordered = line.match(UNORDERED_LIST_RE);
  if (unordered) return { ordered: false, text: unordered[1] };

  return null;
}

function isTableStart(lines: string[], index: number) {
  return Boolean(
    lines[index]?.includes('|') &&
      lines[index + 1]?.includes('|') &&
      isTableDivider(lines[index + 1]),
  );
}

function isTableDivider(line: string) {
  const cells = splitTableRow(line).filter(Boolean);
  return cells.length > 0 && cells.every((cell) => TABLE_DIVIDER_CELL_RE.test(cell));
}

function splitTableRow(line: string) {
  let row = line.trim();
  if (row.startsWith('|')) row = row.slice(1);
  if (row.endsWith('|')) row = row.slice(0, -1);
  return row.split('|').map((cell) => cell.trim());
}

function renderBlock(block: MarkdownBlock, index: number) {
  const key = `markdown-block-${index}`;

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
          <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
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
      <table key={key}>
        <thead>
          <tr>
            {header.map((cell, cellIndex) => (
              <th key={`${key}-head-${cellIndex}`}>{renderInline(cell, `${key}-head-${cellIndex}`)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bodyRows.map((row, rowIndex) => (
            <tr key={`${key}-row-${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td key={`${key}-cell-${rowIndex}-${cellIndex}`}>
                  {renderInline(cell, `${key}-cell-${rowIndex}-${cellIndex}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return <hr key={key} />;
}

function renderHeading(depth: number, text: string, key: string) {
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

function countBackticks(text: string, start: number) {
  let count = 0;
  while (text[start + count] === '`') count += 1;
  return count;
}

function getSafeHref(href: string) {
  const normalizedHref = href.trim();
  if (/^(https?:|mailto:|tel:)/i.test(normalizedHref)) return normalizedHref;
  if (/^(#|\/|\.\/|\.\.\/)/.test(normalizedHref)) return normalizedHref;
  return null;
}
