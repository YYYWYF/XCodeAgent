/** 允许 Electron 的 Vite 构建嵌入验收器原始源码，供同轮证据绑定。 */
declare module '*?raw' {
  const source: string
  export default source
}
