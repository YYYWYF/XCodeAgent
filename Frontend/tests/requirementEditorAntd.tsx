import { Input as OriginalInput } from 'antd/es/index'
import type { ReactElement, ReactNode } from 'react'

export * from 'antd/es/index'

// 仅替换需要浏览器布局或 Portal 的表面；需求组件状态和真实 Form 绑定保持运行。
/** 在无 DOM 测试中保留输入框原始受控属性。 */
function TextArea({ autoSize: _autoSize, showCount: _showCount, ...props }: Record<string, unknown>): ReactElement {
  return <textarea {...props} />
}
export const Input = Object.assign(OriginalInput, { TextArea })

/** 用宿主按钮驱动真实组件事件，不执行浏览器布局。 */
export function Button({ children, htmlType, onClick, disabled }: Record<string, unknown> & { children?: ReactNode }): ReactElement {
  return <button onClick={onClick as () => void} disabled={!!disabled} type={htmlType as 'button' | undefined}>{children}</button>
}

/** 保留浮层开关及确认/取消事件，避免 Node 环境创建 DOM Portal。 */
export function Modal({ open, children, footer, ...props }: Record<string, unknown> & { children?: ReactNode; footer?: ReactNode }): ReactElement | null {
  return open ? <section {...props} data-overlay="modal">{children}{footer}</section> : null
}

/** 隔离悬浮卡片的定位依赖，测试直接核对生产组件传入的触发方式和指引内容。 */
export function Popover({ children }: { children?: ReactNode; [key: string]: unknown }): ReactElement {
  return <div data-overlay="popover">{children}</div>
}

/** 操作说明只保留触发元素，避免测试创建 DOM 浮层。 */
export function Tooltip({ children }: { children?: ReactNode; [key: string]: unknown }): ReactElement {
  return <>{children}</>
}
