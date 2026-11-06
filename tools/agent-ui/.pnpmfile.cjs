const path = require('node:path')
const manifest = require('../../vendor/agent-ui/manifest.json')
const packages = new Map(manifest.packages.map((item) => [item.name, item.file]))

/** 将六个 Agent UI 包映射到本工程成品包，保留第三方依赖和 peer 范围。 */
function readPackage(pkg) {
  for (const field of ['dependencies', 'optionalDependencies']) {
    for (const name of Object.keys(pkg[field] || {})) {
      const file = packages.get(name)
      // 根工程已声明的相对 tarball 路径保持原样，避免 importer 锁记录带入本机绝对路径。
      if (file && !pkg[field][name].startsWith('file:')) {
        pkg[field][name] = 'file:' + path.resolve(__dirname, '../../vendor/agent-ui', file)
      }
    }
  }
  return pkg
}

module.exports = { hooks: { readPackage } }
