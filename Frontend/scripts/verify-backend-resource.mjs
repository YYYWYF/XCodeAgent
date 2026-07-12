/* eslint-disable @typescript-eslint/explicit-function-return-type */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDir, '..')
const supportedPlatforms = new Set(['win32', 'darwin'])
const platform = resolvePlatform()
const backendResourceDir = path.join(frontendRoot, 'resources', 'backend', platform)
const executableName = platform === 'win32' ? 'xcodeagent-backend.exe' : 'xcodeagent-backend'
const bundledSkillsDir = path.join(
  backendResourceDir,
  '_internal',
  'app',
  'builtin_skills'
)
const requiredPaths = [
  path.join(backendResourceDir, executableName),
  path.join(backendResourceDir, '.env'),
  path.join(backendResourceDir, '_internal'),
  bundledSkillsDir
]

const missingPaths = requiredPaths.filter((filePath) => !fs.existsSync(filePath))

if (missingPaths.length > 0) {
  console.error('Missing packaged backend resources:')
  for (const filePath of missingPaths) {
    console.error(`- ${path.relative(frontendRoot, filePath)}`)
  }
  console.error('')
  console.error(`Build the ${platform} backend first:`)
  console.error(buildHint(platform))
  process.exit(1)
}

console.log(
  `Packaged backend resources found at ${path.relative(frontendRoot, backendResourceDir)}`
)

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

function buildHint(platformValue) {
  if (platformValue === 'win32') {
    return 'powershell -ExecutionPolicy Bypass -File ../scripts/build-backend-win.ps1'
  }
  return 'bash ../scripts/build-backend-mac.sh'
}
