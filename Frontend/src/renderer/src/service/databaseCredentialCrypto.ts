import type { ApplicationConfig, ApplicationSchemaConfig } from '../typings'

const SECRET_PREFIX = 'xcodeagent-secret'
const SECRET_VERSION = 'v1'
const SECRET_ALGORITHM = 'rsa-oaep-256'
const PUBLIC_ALGORITHM = 'RSA-OAEP-256'

type DatabaseEncryptionMetadata = {
  enabled: boolean
  algorithm: string
  key_id: string
  public_key: string
}

type BackendHealth = {
  database_encryption?: Partial<DatabaseEncryptionMetadata>
}

/** 获取当前 Python Backend 的数据库加密公钥元数据。 */
export async function getBackendDatabasePublicKey(): Promise<DatabaseEncryptionMetadata> {
  const baseUrl = window.xcodeAgent?.agentBaseUrl?.replace(/\/$/, '') || '/api/agent'
  let response: Response
  try {
    response = await fetch(`${baseUrl}/health`, { method: 'GET', cache: 'no-store' })
  } catch {
    throw new Error('无法连接 Python Backend，不能安全保存数据库密码。')
  }
  if (!response.ok) {
    throw new Error('Python Backend 未返回可用的数据库加密公钥。')
  }
  let health: BackendHealth
  try {
    health = (await response.json()) as BackendHealth
  } catch {
    throw new Error('Python Backend 的数据库加密公钥响应格式无效。')
  }
  const metadata = health.database_encryption
  if (
    metadata?.enabled !== true ||
    metadata.algorithm !== PUBLIC_ALGORITHM ||
    !metadata.key_id ||
    !metadata.public_key
  ) {
    throw new Error('Python Backend 未启用受支持的数据库密码加密。')
  }
  return metadata as DatabaseEncryptionMetadata
}

/** 判断密码是否已经使用当前版本的密文信封格式。 */
export function isEncryptedPassword(value: string): boolean {
  const [prefix, version, algorithm, keyId, ciphertext, ...extra] = value.split(':')
  return Boolean(
    prefix === SECRET_PREFIX &&
      version === SECRET_VERSION &&
      algorithm === SECRET_ALGORITHM &&
      keyId &&
      ciphertext &&
      extra.length === 0
  )
}

/** 使用 RSA-OAEP-SHA256 加密单个 plantMode 密码并生成版本化字符串。 */
export async function encryptPlantModePassword(
  password: string,
  metadata?: DatabaseEncryptionMetadata
): Promise<string> {
  if (!password) throw new Error('数据库密码不能为空。')
  if (isEncryptedPassword(password)) return password
  if (password.startsWith(`${SECRET_PREFIX}:`)) {
    throw new Error('数据库密码密文格式无效，应用配置尚未保存。')
  }
  const publicMetadata = metadata || (await getBackendDatabasePublicKey())
  if (
    publicMetadata.algorithm !== PUBLIC_ALGORITHM ||
    !publicMetadata.key_id ||
    !publicMetadata.public_key
  ) {
    throw new Error('数据库加密公钥元数据无效。')
  }

  const encodedPassword = new TextEncoder().encode(password)
  let publicKey: CryptoKey
  try {
    publicKey = await window.crypto.subtle.importKey(
      'spki',
      pemPublicKeyBytes(publicMetadata.public_key),
      { name: 'RSA-OAEP', hash: 'SHA-256' },
      false,
      ['encrypt']
    )
  } catch {
    throw new Error('Python Backend 返回的数据库加密公钥无效。')
  }
  const rsaAlgorithm = publicKey.algorithm as RsaHashedKeyAlgorithm
  if (rsaAlgorithm.modulusLength !== 3072) {
    throw new Error('Python Backend 返回的数据库加密公钥不是 RSA-3072。')
  }
  const maximumPlaintextBytes = rsaAlgorithm.modulusLength / 8 - 2 * 32 - 2
  if (encodedPassword.byteLength > maximumPlaintextBytes) {
    throw new Error(`数据库密码过长，RSA-OAEP-SHA256 最多支持 ${maximumPlaintextBytes} 字节。`)
  }

  let encrypted: ArrayBuffer
  try {
    encrypted = await window.crypto.subtle.encrypt({ name: 'RSA-OAEP' }, publicKey, encodedPassword)
  } catch {
    throw new Error('数据库密码加密失败，应用配置尚未保存。')
  }
  return [
    SECRET_PREFIX,
    SECRET_VERSION,
    SECRET_ALGORITHM,
    publicMetadata.key_id,
    base64UrlEncode(new Uint8Array(encrypted))
  ].join(':')
}

/** 复制应用 schema，仅加密 plantMode.pwd，供所有后续持久化共用。 */
export async function encryptSensitiveDatasourceFields(
  schema: ApplicationSchemaConfig
): Promise<ApplicationSchemaConfig> {
  const plantMode = schema.datasource.db.plantMode
  if (!plantMode) return schema
  const encryptedPassword = await encryptPlantModePassword(plantMode.pwd)
  return {
    ...schema,
    datasource: {
      ...schema.datasource,
      db: {
        ...schema.datasource.db,
        plantMode: {
          ...plantMode,
          pwd: encryptedPassword
        }
      }
    }
  }
}

/** 加密完整应用索引对象，并让顶层 datasource 与嵌套 schema 共享密文配置。 */
export async function encryptApplicationForPersistence(
  application: ApplicationConfig
): Promise<ApplicationConfig> {
  const encryptedTopLevel = await encryptSensitiveDatasourceFields(application)
  return {
    ...application,
    datasource: encryptedTopLevel.datasource,
    schema: {
      ...application.schema,
      datasource: encryptedTopLevel.datasource
    }
  }
}

/** 把 PEM 公钥正文转换为 WebCrypto 所需的 DER 字节。 */
function pemPublicKeyBytes(publicKeyPem: string): ArrayBuffer {
  const encoded = publicKeyPem
    .replace(/-----BEGIN PUBLIC KEY-----/g, '')
    .replace(/-----END PUBLIC KEY-----/g, '')
    .replace(/\s/g, '')
  if (!encoded) throw new Error('empty public key')
  const binary = window.atob(encoded)
  return Uint8Array.from(binary, (character) => character.charCodeAt(0)).buffer
}

/** 把密文字节编码为无填充的 base64url。 */
function base64UrlEncode(value: Uint8Array): string {
  const binary = String.fromCharCode(...value)
  return window.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
