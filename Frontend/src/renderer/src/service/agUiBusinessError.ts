/** 区分 Backend 已响应的业务失败和传输失败，不按错误文案猜测连接状态。 */
export class AgUiBusinessError extends Error {
  /** 保留当前业务校验的原始错误。 */
  constructor(message: string) {
    super(message)
    this.name = 'AgUiBusinessError'
  }
}
