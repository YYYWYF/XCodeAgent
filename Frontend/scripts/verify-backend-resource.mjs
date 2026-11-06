/* eslint-disable @typescript-eslint/explicit-function-return-type */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDir, '..')
const supportedPlatforms = new Set(['win32', 'darwin'])
const appEnv = process.argv
  .find((argument) => argument.startsWith('--env='))
  ?.slice('--env='.length)
if (!['dev', 'st', 'uat', 'prd'].includes(appEnv)) {
  console.error('Backend resource verification requires --env=dev|st|uat|prd.')
  process.exit(1)
}
const envFilename = appEnv === 'dev' ? '.env' : `.env.${appEnv}`
const platform = resolvePlatform()
const architecture = resolveArchitecture(platform)
const resourcePlatform = platform === 'darwin' ? `${platform}-${architecture}` : platform
const backendResourceDir = path.join(frontendRoot, 'resources', 'backend', resourcePlatform)
const executableName =
  platform === 'win32' ? 'devagentstudio-backend.exe' : 'devagentstudio-backend'
const bundledSkillsDir = path.join(backendResourceDir, '_internal', 'app', 'builtin_skills')
const requiredPaths = [
  [path.join(backendResourceDir, executableName), 'file'],
  [path.join(backendResourceDir, envFilename), 'file'],
  [path.join(backendResourceDir, '_internal'), 'directory'],
  [bundledSkillsDir, 'directory']
]

// 文件与目录必须满足资源类型，避免仅有同名目录时误通过构建门禁。
const missingPaths = requiredPaths.filter(([filePath, kind]) => {
  try {
    const stat = fs.statSync(filePath)
    return kind === 'file' ? !stat.isFile() : !stat.isDirectory()
  } catch {
    return true
  }
})

if (missingPaths.length > 0) {
  console.error('Missing packaged backend resources:')
  for (const [filePath] of missingPaths) {
    console.error(`- ${path.relative(frontendRoot, filePath)}`)
  }
  console.error('')
  console.error(`Build the ${platform} backend first:`)
  console.error(buildHint(platform))
  process.exit(1)
}

// 资源目录只能携带当前环境的真实配置，防止历史 staging 内容混入安装包。
const unexpectedFiles = fs
  .readdirSync(backendResourceDir)
  .filter((name) => /^\.env(?:\..*)?$/i.test(name) && name !== envFilename)
if (unexpectedFiles.length > 0) {
  console.error(`Unexpected backend environment files: ${unexpectedFiles.join(', ')}`)
  process.exit(1)
}

if (platform === 'darwin') {
  try {
    fs.accessSync(path.join(backendResourceDir, executableName), fs.constants.X_OK)
  } catch {
    console.error(
      `Packaged macOS backend is not executable: ${path.join(backendResourceDir, executableName)}`
    )
    process.exit(1)
  }
}

console.log(
  `Packaged backend resources found at ${path.relative(frontendRoot, backendResourceDir)}`
)

// 读取构建目标平台，拒绝不支持的平台。
function resolvePlatform() {
  const platformArgument = process.argv.find((argument) => argument.startsWith('--platform='))
  const platformValue = platformArgument?.slice('--platform='.length) || process.platform

  if (!supportedPlatforms.has(platformValue)) {
    console.error(`Unsupported backend resource platform: ${platformValue}`)
    console.error('Supported platforms: win32, darwin')
    process.exit(1)
  }

  return platformValue
}

// 解析并校验当前打包目标架构，防止把另一种 CPU 的冻结后端装入应用。
function resolveArchitecture(platformValue) {
  const archArgument = process.argv.find((argument) => argument.startsWith('--arch='))
  const archValue = archArgument?.slice('--arch='.length) || process.arch
  const supportedArchitectures =
    platformValue === 'darwin' ? new Set(['x64', 'arm64']) : new Set(['x64'])

  if (!supportedArchitectures.has(archValue)) {
    console.error(`Unsupported ${platformValue} backend architecture: ${archValue}`)
    console.error(`Supported architectures: ${[...supportedArchitectures].join(', ')}`)
    process.exit(1)
  }
  return archValue
}

// 给出包含当前环境的重新 staging 命令。
function buildHint(platformValue) {
  if (platformValue === 'win32') {
    return `powershell -ExecutionPolicy Bypass -File ../scripts/build-backend-win.ps1 -Env ${appEnv}`
  }
  return `bash ../scripts/build-backend-mac.sh ${architecture} --env ${appEnv}`
}
