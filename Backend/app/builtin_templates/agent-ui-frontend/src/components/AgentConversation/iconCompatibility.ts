/** 兼容旧版 Ant Design Icons 对 React 指针捕获属性的必填类型声明。 */
function ignoreIconPointerCapture(): void {
  return undefined
}

export const agentIconCompatibilityProps = {
  onPointerEnterCapture: ignoreIconPointerCapture,
  onPointerLeaveCapture: ignoreIconPointerCapture
}
