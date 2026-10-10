import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../../../utils'
import './WorkbenchDrawer.less'

type Props = {
  /** 无障碍名称，用于区分不同抽屉。 */
  label: string
  /** 宽度档位：列表型内容用 medium，表单/编辑型内容用 wide。 */
  size?: 'medium' | 'wide'
  children: ReactNode
}

/**
 * 工作台左侧抽屉外壳：从折叠栏右侧浮出、覆盖在对话区之上，与临时对话/历史对话/
 * 数据源保持同一套定位、阴影与进场动效。
 *
 * 只提供外壳，不渲染头部 —— 各页面自带标题头，再加一层会出现两层标题。关闭入口由
 * 各页面头部的关闭按钮承担（页面通过可选的 onClose 接收）。
 */
export default function WorkbenchDrawer({ label, size = 'medium', children }: Props): ReactElement {
  return (
    <section
      aria-label={label}
      className={cx('workbench-drawer', `workbench-drawer-${size}`)}
      role="region"
    >
      {children}
    </section>
  )
}
