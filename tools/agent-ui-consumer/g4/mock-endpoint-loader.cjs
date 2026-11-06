/** 仅在独立 T24 验收编译中替换唯一 Host 地址，不修改目标磁盘或包实现。 */
module.exports = function mockEndpointLoader(code) {
  this.cacheable?.()
  if (code.split('endpoint=""').length !== 2) {
    throw new Error('T24 Mock preview requires exactly one empty Host endpoint')
  }
  return code.replace('endpoint=""', 'endpoint="/agent"')
}
