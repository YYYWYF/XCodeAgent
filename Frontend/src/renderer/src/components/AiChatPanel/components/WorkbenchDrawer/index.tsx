import { useEffect, useRef } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../../../utils'
import './WorkbenchDrawer.less'

type Props = {
  /** 无障碍名称，用于区分不同抽屉。 */
  label: string
  /** 宽度档位：列表型内容用 medium，表单/编辑型内容用 wide。 */
  size?: 'medium' | 'wide'
  /** 点击抽屉外部时触发关闭（可选）。传入后即启用"点击空白收起"行为。 */
  onClose?: () => void
  children: ReactNode
}

/**
 * 工作台左侧抽屉外壳：从折叠栏右侧浮出、覆盖在对话区之上，与临时对话/历史对话/
 * 数据源保持同一套定位、阴影与进场动效。
 *
 * 只提供外壳，不渲染头部 —— 各页面自带标题头，再加一层会出现两层标题。关闭入口由
 * 各页面头部的关闭按钮承担（页面通过可选的 onClose 接收）。
 *
 * 传入 onClose 时，点击抽屉外的任意位置（对话区/右侧栏等）会触发 onClose 让父组件
 * 收起抽屉。监听用 mousedown 而非 click：click 会在 mouseup 才触发，用户按下鼠标
 * 拖动选择文本时（down 在抽屉内、up 在外）不应被误判为"点击外部"。
 */
export default function WorkbenchDrawer({ label, size = 'medium', onClose, children }: Props): ReactElement {
  const rootRef = useRef<HTMLElement | null>(null)
  // 用 ref 持有最新 onClose，避免 effect 因 onClose 引用变化反复挂载/卸载监听。
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!onClose) return
    // mousedown 在捕获阶段处理：若目标在抽屉 DOM 内则放行；在外则触发关闭。
    // 必须在捕获阶段而不是冒泡阶段——抽屉内某些按钮（如保存）可能在冒泡阶段
    // stopPropagation，但不影响捕获阶段判断"点击位置是否在抽屉外"。
    const handleMouseDown = (event: MouseEvent): void => {
      const root = rootRef.current
      if (!root) return
      const target = event.target as Node | null
      if (!target) return
      if (root.contains(target)) return
      // antd 浮层（Modal 确认框/Select 下拉/Popover/Tooltip 等）默认 portal 到 body，
      // DOM 在抽屉外但语义上属于抽屉内的交互。点击这些浮层不应关闭抽屉。
      // 覆盖 antd 主要浮层容器类名。
      if (target instanceof Element) {
        const floating = target.closest(
          '.ant-modal-wrap, .ant-modal-mask, .ant-select-dropdown, ' +
            '.ant-picker-dropdown, .ant-popover, .ant-tooltip, .ant-dropdown, ' +
            '.ant-cascader-dropdown, .ant-tree-select-dropdown, .ant-message, ' +
            '.ant-notification, .ant-popconfirm'
        )
        if (floating) return
        // 侧栏菜单按钮（技能/文件/设置/数据源/外部API）是 toggle：mousedown 关闭后
        // click 又会打开，导致抽屉闪烁。跳过它们，让 click 阶段的 toggle 逻辑独立决定。
        if (target.closest('[data-sidebar-menu="true"]')) return
      }
      onCloseRef.current?.()
    }
    document.addEventListener('mousedown', handleMouseDown, true)
    return () => {
      document.removeEventListener('mousedown', handleMouseDown, true)
    }
  }, [onClose])

  return (
    <section
      aria-label={label}
      className={cx('workbench-drawer', `workbench-drawer-${size}`)}
      ref={rootRef}
      role="region"
    >
      {children}
    </section>
  )
}
