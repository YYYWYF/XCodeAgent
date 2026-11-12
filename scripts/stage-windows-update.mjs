import { mkdtemp, readdir, rename, rmdir } from 'node:fs/promises'
import path from 'node:path'
import { prepareCdn } from '../Frontend/scripts/app-update-cdn.mjs'

try {
  const dist = path.resolve(process.argv[2] ?? path.join(import.meta.dirname, '../Frontend/dist'))
  const { version, files } = await prepareCdn(dist, ['latest.yml'])
  const installers = [...files.keys()].filter(
    (name) => name !== 'latest.yml' && !name.endsWith('.blockmap')
  )
  if (installers.length !== 1 || !/\.exe$/i.test(installers[0]))
    throw new Error('Windows 更新清单必须引用一个 EXE 安装包')

  // 先读取全部产物，避免把随后创建的临时目录也纳入归档。
  const artifacts = await readdir(dist)
  if (artifacts.includes(version)) throw new Error(`版本目录已存在：${version}`)
  const release = path.join(dist, version)
  const temporary = await mkdtemp(path.join(dist, '.windows-update-'))
  const moved = []
  try {
    // 全部产物移入临时目录后，再将完整目录发布为版本目录。
    for (const name of artifacts) {
      await rename(path.join(dist, name), path.join(temporary, name))
      moved.push(name)
    }
    await rename(temporary, release)
  } catch (error) {
    // 归档失败时恢复已移动产物的原路径，保留重试所需文件。
    for (const name of moved.reverse()) {
      await rename(path.join(temporary, name), path.join(dist, name))
    }
    await rmdir(temporary)
    throw error
  }
  console.log(`Windows 版本 ${version} 的产物目录：${release}`)
} catch (error) {
  console.error(`Windows 更新产物准备失败：${error.message}`)
  process.exitCode = 1
}
