const path = require('node:path')
const manifest = require('../../vendor/agent-ui/manifest.json')
const packages = new Map(manifest.packages.map((item) => [item.name, item.file]))

/** 独立消费者只从成品包解析内部依赖，安装不依赖 Creator 工具目录。 */
function readPackage(pkg) {
  for (const field of ['dependencies', 'optionalDependencies']) {
    for (const name of Object.keys(pkg[field] || {})) {
      const file = packages.get(name)
      if (file && !pkg[field][name].startsWith('file:')) {
        pkg[field][name] = 'file:' + path.resolve(__dirname, '../../vendor/agent-ui', file)
      }
    }
  }
  return pkg
}

module.exports = { hooks: { readPackage } }
