/* eslint @typescript-eslint/explicit-function-return-type: off -- Node ESM scripts cannot use TypeScript return annotations. */
import fs from 'node:fs/promises'
import { createReadStream } from 'node:fs'
import { createHash } from 'node:crypto'
import http from 'node:http'
import path from 'node:path'
import yaml from 'js-yaml'

/** 仅允许更新产物的单层文件名，避免模拟服务暴露打包后端及其配置。 */
function artifactName(value) {
  if (
    typeof value !== 'string' ||
    !value ||
    value !== path.basename(value) ||
    /[\\/:]/.test(value) ||
    value.includes('\0') ||
    !/\.(exe|zip|dmg)(\.blockmap)?$/i.test(value)
  )
    throw new Error(`非法更新产物名称：${value}`)
  return value
}

/** 校验发布清单引用的文件大小及 SHA-512，并建立只读白名单。 */
export async function prepareCdn(directory) {
  const root = path.resolve(directory)
  const files = new Map()
  let version
  for (const name of ['latest.yml', 'latest-mac.yml']) {
    let stat
    try {
      stat = await fs.lstat(path.join(root, name))
    } catch (error) {
      if (error.code === 'ENOENT') continue
      throw error
    }
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 262144)
      throw new Error(`无效清单：${name}`)
    const bytes = await fs.readFile(path.join(root, name))
    const manifest = yaml.load(bytes.toString('utf8'), { schema: yaml.JSON_SCHEMA })
    if (
      !manifest ||
      typeof manifest.version !== 'string' ||
      !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(manifest.version) ||
      !Array.isArray(manifest.files) ||
      !manifest.files.length
    )
      throw new Error(`清单缺少版本或文件：${name}`)
    if (version && version !== manifest.version) throw new Error('Windows 与 Mac 清单版本不一致')
    version = manifest.version
    for (const item of manifest.files) {
      const filename = artifactName(item.url)
      const location = path.join(root, filename)
      const info = await fs.lstat(location)
      if (
        !info.isFile() ||
        info.isSymbolicLink() ||
        !Number.isSafeInteger(item.size) ||
        info.size !== item.size
      )
        throw new Error(`文件大小不匹配：${filename}`)
      const hash = createHash('sha512')
      for await (const chunk of createReadStream(location)) hash.update(chunk)
      if (hash.digest('base64') !== item.sha512) throw new Error(`SHA-512 不匹配：${filename}`)
      files.set(filename, { location, size: info.size })
      const blockmap = `${filename}.blockmap`
      try {
        const block = await fs.lstat(path.join(root, blockmap))
        if (!block.isFile() || block.isSymbolicLink()) throw new Error(`无效 blockmap：${blockmap}`)
        files.set(blockmap, { location: path.join(root, blockmap), size: block.size })
      } catch (error) {
        if (error.code !== 'ENOENT') throw error
      }
    }
    files.set(name, { bytes, size: bytes.length })
  }
  if (!version) throw new Error('未找到 latest.yml 或 latest-mac.yml，请先生成更新包')
  return { files, version }
}

/** 解析单段字节范围；非法或超界请求返回 null。 */
export function parseRange(value, size) {
  const match = /^bytes=(\d*)-(\d*)$/.exec(value)
  if (!match || (!match[1] && !match[2])) return null
  const suffix = !match[1]
  const start = suffix ? Math.max(0, size - Number(match[2])) : Number(match[1])
  const end = suffix || !match[2] ? size - 1 : Math.min(Number(match[2]), size - 1)
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end || start >= size)
    return null
  return { start, end }
}

/** 创建仅提供白名单发布文件的本地 CDN，支持 HEAD 及单段 Range。 */
export function createCdnServer(prepared) {
  return http.createServer((request, response) => {
    if (!['GET', 'HEAD'].includes(request.method)) {
      response.writeHead(405, { Allow: 'GET, HEAD' }).end()
      return
    }
    let name
    try {
      name = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname).slice(1)
    } catch {
      response.writeHead(400).end()
      return
    }
    const file = prepared.files.get(name)
    if (!file) {
      response.writeHead(404).end()
      return
    }
    const range = request.headers.range ? parseRange(request.headers.range, file.size) : undefined
    if (range === null) {
      response.writeHead(416, { 'Content-Range': `bytes */${file.size}` }).end()
      return
    }
    const start = range?.start ?? 0
    const end = range?.end ?? file.size - 1
    const headers = {
      'Content-Type': name.endsWith('.yml') ? 'application/yaml' : 'application/octet-stream',
      'Content-Length': end - start + 1,
      'Accept-Ranges': 'bytes',
      'Cache-Control': 'no-store'
    }
    if (range) headers['Content-Range'] = `bytes ${start}-${end}/${file.size}`
    response.writeHead(range ? 206 : 200, headers)
    if (request.method === 'HEAD') {
      response.end()
      return
    }
    if (file.bytes) {
      response.end(file.bytes.subarray(start, end + 1))
      return
    }
    const stream = createReadStream(file.location, { start, end })
    stream.on('error', () => response.destroy())
    response.on('close', () => stream.destroy())
    stream.pipe(response)
  })
}
