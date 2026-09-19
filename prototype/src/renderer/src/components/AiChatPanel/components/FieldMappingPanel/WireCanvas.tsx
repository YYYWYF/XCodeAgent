import { Fragment, useEffect, useId, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

/** 画布几何常量：参数行 32px、行距 6px；列标题行 26px；左侧行标题列 36px；中段走廊 96px。 */
const NODE_HEIGHT = 32
const NODE_GAP = 6
const HEADER_HEIGHT = 26
const CORNER_WIDTH = 36
const CORRIDOR_WIDTH = 96
const REGION_GAP = 14

/** 连线节点的视觉状态：pending=待连接源节点；danger=必填未连接；ready=可连接目标。 */
export type WireNodeTone = 'normal' | 'pending' | 'danger' | 'ready'

/** 连线的视觉类别：manual=人工连接（实线），auto=AI 建议（虚线）。 */
export type WireTone = 'manual' | 'auto'

/** 画布上的一个参数行：id 供连线定位（带分区前缀），content 为行内内容。 */
export type WireNode = {
  id: string
  content?: ReactElement
  tone?: WireNodeTone
}

/** 一个横向分区（入参/出参）：行标题 + 左右两列的参数行，两列按行号严格对齐。 */
export type WireRegion = {
  id: string
  rowTitle: string
  leftRows: WireNode[]
  rightRows: WireNode[]
}

/** 一条连线：from/to 为节点 id，箭头始终落在 to 端。 */
export type Wire = {
  id: string
  from: string
  to: string
  tone: WireTone
}

type Props = {
  /** 列标题：应用API / 外部API。 */
  columnTitles: [string, string]
  regions: WireRegion[]
  wires: Wire[]
  /** 待连接的源节点：再点一次或点击空白处取消。 */
  pending: { side: 'left' | 'right'; id: string } | null
  /** 只读态：节点与连线均不可操作。 */
  disabled?: boolean
  onNodeClick: (side: 'left' | 'right', id: string) => void
  /** 点击连线：断开该条映射。 */
  onWireClick: (wire: Wire) => void
  /** 点击画布空白处：取消待连接状态。 */
  onCanvasClick: () => void
}

/** 连线色调对应的描边颜色：与 LESS 中的连线配色保持一致（SVG 属性无法用类选择器部分覆盖 marker）。 */
const TONE_COLORS: Record<WireTone, string> = {
  manual: 'var(--wb-accent)',
  auto: 'var(--wb-accent)'
}

/**
 * 双列连线画布：按「列标题（应用API/外部API）× 行标题（入参/出参）」的 2×2 矩阵排布，
 * 四个单元格行列严格对齐——分区高度取两列行数的较大值，短的一侧留空占位。中段走廊
 * 用 SVG 贝塞尔曲线画取值连线；端点纵坐标按行高累计推算，宽度变化由 resize 跟随。
 */
export default function WireCanvas({
  columnTitles,
  regions,
  wires,
  pending,
  disabled = false,
  onNodeClick,
  onWireClick,
  onCanvasClick
}: Props): ReactElement {
  // useId 产物含冒号，不能直接用于 SVG marker 的 url(#) 引用，替换为安全字符。
  const rawId = useId()
  const markerId = rawId.replace(/:/g, '')
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(0)

  // 监听容器宽度：分栏拖动 / 窗口缩放时连线端点 x 坐标实时跟随。
  useEffect(() => {
    const element = hostRef.current
    if (!element) return
    const update = (): void => setWidth(element.clientWidth)
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])

  /** 预计算每个分区的行数，生成整列行高序列并累计出每行顶部纵坐标。 */
  const regionMetas = regions.map((region) => {
    const maxRows = Math.max(region.leftRows.length, region.rightRows.length)
    return { region, maxRows }
  })
  const rowHeights: number[] = [HEADER_HEIGHT]
  const regionRowStarts: number[] = []
  regionMetas.forEach((meta) => {
    regionRowStarts.push(rowHeights.length)
    if (meta.maxRows > 0) {
      rowHeights.push(...Array.from({ length: meta.maxRows }, () => NODE_HEIGHT))
    }
    rowHeights.push(REGION_GAP)
  })
  const rowTops: number[] = []
  rowHeights.reduce((top, height) => {
    rowTops.push(top)
    return top + height + NODE_GAP
  }, 0)
  const canvasHeight = rowHeights.reduce((total, height) => total + height, 0) + NODE_GAP * (rowHeights.length - 1)

  // 参数列宽与连线端点：端点必须落在参数区块的左右边缘上。行标题列（36px）会把
  // 两列整体推向右侧，走廊中心不在画布正中，不能用 width/2 推算，否则线头够不到
  // 左列、箭头压到右列上。
  const columnWidth = Math.max(0, (width - CORNER_WIDTH - CORRIDOR_WIDTH) / 2)
  const leftEdgeX = CORNER_WIDTH + columnWidth
  const rightEdgeX = leftEdgeX + CORRIDOR_WIDTH

  /** 按节点 id 找端点坐标：先查左列再查右列，找不到返回 null（数据尚未就绪时跳过绘制）。 */
  const endpointOf = (id: string): { x: number; y: number } | null => {
    for (let index = 0; index < regionMetas.length; index += 1) {
      const meta = regionMetas[index]
      const startRow = regionRowStarts[index]
      const leftIndex = meta.region.leftRows.findIndex((node) => node.id === id)
      if (leftIndex >= 0) {
        return { x: leftEdgeX, y: rowTops[startRow + leftIndex] + NODE_HEIGHT / 2 }
      }
      const rightIndex = meta.region.rightRows.findIndex((node) => node.id === id)
      if (rightIndex >= 0) {
        return { x: rightEdgeX, y: rowTops[startRow + rightIndex] + NODE_HEIGHT / 2 }
      }
    }
    return null
  }

  /** 点击画布空白：取消待连接状态。 */
  const handleBackgroundClick = (): void => {
    onCanvasClick()
  }

  return (
    <div
      className={cx('field-mapping-canvas', disabled && 'disabled')}
      onClick={handleBackgroundClick}
      ref={hostRef}
      style={{ height: canvasHeight }}
    >
      <svg className={cx('field-mapping-wires')} height={canvasHeight} width={width}>
        <defs>
          {(['manual', 'auto'] as WireTone[]).map((tone) => (
            <marker
              fill={TONE_COLORS[tone]}
              id={`${markerId}-${tone}`}
              key={tone}
              markerHeight={7}
              markerWidth={7}
              orient="auto"
              refX={8}
              refY={3.5}
              viewBox="0 0 8 8"
            >
              <path d="M0 0 L8 3.5 L0 7 Z" />
            </marker>
          ))}
        </defs>
        {wires.map((wire) => {
          const from = endpointOf(wire.from)
          const to = endpointOf(wire.to)
          if (!from || !to) return null
          // 三次贝塞尔：控制点沿水平方向外推，让连线在走廊里自然弯折。
          const bend = Math.max(24, Math.abs(to.x - from.x) / 2)
          const path = `M ${from.x} ${from.y} C ${from.x + (to.x > from.x ? bend : -bend)} ${from.y}, ${
            to.x + (to.x > from.x ? -bend : bend)
          } ${to.y}, ${to.x} ${to.y}`
          return (
            <g
              className={cx('field-mapping-wire', wire.tone)}
              key={wire.id}
              onClick={
                disabled
                  ? undefined
                  : (event) => {
                      event.stopPropagation()
                      onWireClick(wire)
                    }
              }
            >
              {/* 加宽的透明命中区：细线难点中，命中区负责整条线的点击断开。 */}
              <path className={cx('field-mapping-wire-hit')} d={path} />
              // 箭头尖端与路径终点对齐（refX 取满宽），不压到目标参数区块上。
              <path
                className={cx('field-mapping-wire-line')}
                d={path}
                fill="none"
                markerEnd={`url(#${markerId}-${wire.tone})`}
                strokeDasharray={wire.tone === 'auto' ? '5 4' : undefined}
              />
              <title>点击断开连接</title>
            </g>
          )
        })}
        {pending && width > 0
          ? (() => {
              // 待连接的虚线引线：从源节点边缘伸向走廊中线，提示当前正在建立连接。
              const from = endpointOf(pending.id)
              if (!from) return null
              const midX = (leftEdgeX + rightEdgeX) / 2
              return (
                <path
                  className={cx('field-mapping-wire-pending')}
                  d={`M ${from.x} ${from.y} L ${midX} ${from.y}`}
                  fill="none"
                />
              )
            })()
          : null}
      </svg>
      <div
        className={cx('field-mapping-canvas-grid')}
        style={{
          gridTemplateColumns: `${CORNER_WIDTH}px minmax(0, 1fr) ${CORRIDOR_WIDTH}px minmax(0, 1fr)`,
          gridTemplateRows: rowHeights.map((height) => `${height}px`).join(' '),
          // 行距必须与端点纵坐标的累计公式一致（每行之间补 NODE_GAP），否则连线错位。
          rowGap: `${NODE_GAP}px`
        }}
      >
        {/* 列标题行：应用API / 外部API */}
        <div />
        <div className={cx('field-mapping-canvas-colhdr')}>{columnTitles[0]}</div>
        <div />
        <div className={cx('field-mapping-canvas-colhdr')}>{columnTitles[1]}</div>
        {regionMetas.map(({ region, maxRows }, index) => {
          const startRow = regionRowStarts[index]
          return (
            <Fragment key={region.id}>
              {maxRows > 0 ? (
                <div
                  className={cx('field-mapping-canvas-rowhdr')}
                  style={{ gridColumn: 1, gridRow: `${startRow + 1} / span ${maxRows}` }}
                >
                  {region.rowTitle}
                </div>
              ) : null}
              {region.leftRows.map((node, rowIndex) => (
                <div
                  aria-disabled={disabled || undefined}
                  className={cx(
                    'field-mapping-node',
                    node.tone && node.tone !== 'normal' && node.tone,
                    disabled && 'off'
                  )}
                  data-node={node.id}
                  key={node.id}
                  onClick={
                    disabled
                      ? undefined
                      : (event) => {
                          event.stopPropagation()
                          onNodeClick('left', node.id)
                        }
                  }
                  role="button"
                  style={{ gridColumn: 2, gridRow: startRow + rowIndex + 1 }}
                  tabIndex={disabled ? undefined : 0}
                >
                  {node.content}
                </div>
              ))}
              {region.rightRows.map((node, rowIndex) => (
                <div
                  aria-disabled={disabled || undefined}
                  className={cx(
                    'field-mapping-node',
                    node.tone && node.tone !== 'normal' && node.tone,
                    disabled && 'off'
                  )}
                  data-node={node.id}
                  key={node.id}
                  onClick={
                    disabled
                      ? undefined
                      : (event) => {
                          event.stopPropagation()
                          onNodeClick('right', node.id)
                        }
                  }
                  role="button"
                  style={{ gridColumn: 4, gridRow: startRow + rowIndex + 1 }}
                  tabIndex={disabled ? undefined : 0}
                >
                  {node.content}
                </div>
              ))}
            </Fragment>
          )
        })}
      </div>
    </div>
  )
}
