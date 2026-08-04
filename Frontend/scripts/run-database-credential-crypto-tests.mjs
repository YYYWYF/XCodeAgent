import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const modulePath = path.join(
  scriptDirectory,
  '..',
  'src',
  'renderer',
  'src',
  'service',
  'databaseCredentialCrypto.ts'
)

// 在 Node WebCrypto 中生成与 Backend 相同规格的测试密钥。
const keyPair = await webcrypto.subtle.generateKey(
  {
    name: 'RSA-OAEP',
    modulusLength: 3072,
    publicExponent: new Uint8Array([1, 0, 1]),
    hash: 'SHA-256'
  },
  true,
  ['encrypt', 'decrypt']
)
const publicKeyDer = await webcrypto.subtle.exportKey('spki', keyPair.publicKey)
const publicKeyBase64 = Buffer.from(publicKeyDer)
  .toString('base64')
  .match(/.{1,64}/g)
  ?.join('\n')
const publicKeyPem = `-----BEGIN PUBLIC KEY-----\n${publicKeyBase64}\n-----END PUBLIC KEY-----\n`
const metadata = {
  enabled: true,
  algorithm: 'RSA-OAEP-256',
  key_id: 'platform-key-v1',
  public_key: publicKeyPem
}

// 把无运行时依赖的 TypeScript 产品模块转译后执行真实导出函数。
const moduleSource = await fs.readFile(modulePath, 'utf8')
const transpiled = ts.transpileModule(moduleSource, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022
  }
}).outputText
globalThis.window = {
  crypto: webcrypto,
  atob: (value) => Buffer.from(value, 'base64').toString('binary'),
  btoa: (value) => Buffer.from(value, 'binary').toString('base64'),
  xcodeAgent: { agentBaseUrl: 'http://127.0.0.1:8000' }
}
globalThis.fetch = async () => ({
  ok: true,
  json: async () => ({ database_encryption: metadata })
})
const compiledModule = { exports: {} }
const moduleFactory = new Function('exports', 'module', transpiled)
moduleFactory(compiledModule.exports, compiledModule)
const cryptoModule = compiledModule.exports

assert.deepEqual(await cryptoModule.getBackendDatabasePublicKey(), metadata)
const healthyFetch = globalThis.fetch
globalThis.fetch = async () => {
  throw new Error('backend unavailable')
}
await assert.rejects(cryptoModule.getBackendDatabasePublicKey(), /无法连接 Python Backend/)
globalThis.fetch = healthyFetch

const password = '数据库-密码-🔐'
const encrypted = await cryptoModule.encryptPlantModePassword(password, metadata)
assert.match(encrypted, /^xcodeagent-secret:v1:rsa-oaep-256:platform-key-v1:/)
assert.equal(cryptoModule.isEncryptedPassword(encrypted), true)

// 使用同一 RSA-OAEP-SHA256 参数解密，验证 Unicode 与 base64url 协议。
const ciphertext = Buffer.from(encrypted.split(':')[4], 'base64url')
const decrypted = await webcrypto.subtle.decrypt(
  { name: 'RSA-OAEP' },
  keyPair.privateKey,
  ciphertext
)
assert.equal(new TextDecoder().decode(decrypted), password)

const environment = { dev: [{ key: 'UNCHANGED', value: '1' }], prod: [] }
const schema = {
  appName: 'crypto-test',
  datasource: {
    type: 'DataBase',
    db: {
      useBuiltin: false,
      plantMode: {
        domain: 'db.local',
        port: 3306,
        userName: 'reader',
        pwd: password,
        schema: 'inventory'
      }
    }
  },
  environment
}
const persistedSchema = await cryptoModule.encryptSensitiveDatasourceFields(schema)
assert.equal(persistedSchema.datasource.db.plantMode.domain, 'db.local')
assert.equal(persistedSchema.datasource.db.plantMode.port, 3306)
assert.equal(persistedSchema.datasource.db.plantMode.userName, 'reader')
assert.equal(persistedSchema.datasource.db.plantMode.schema, 'inventory')
assert.equal(persistedSchema.environment, environment)
assert.equal(JSON.stringify(persistedSchema).includes(password), false)
assert.equal(
  JSON.stringify({ ...persistedSchema, schema: persistedSchema }).includes(password),
  false
)
const persistedApplication = await cryptoModule.encryptApplicationForPersistence({
  ...schema,
  id: 'app-1',
  name: 'crypto-test',
  schema,
  enableAuth: false,
  enableTracking: false,
  pages: [],
  defaultPage: '',
  createdAt: 1
})
assert.equal(
  persistedApplication.datasource.db.plantMode.pwd,
  persistedApplication.schema.datasource.db.plantMode.pwd
)
assert.equal(JSON.stringify(persistedApplication).includes(password), false)

await assert.rejects(cryptoModule.encryptPlantModePassword('', metadata), /数据库密码不能为空/)
await assert.rejects(
  cryptoModule.encryptPlantModePassword('xcodeagent-secret:v1:broken', metadata),
  /密文格式无效/
)
await assert.rejects(
  cryptoModule.encryptPlantModePassword(password, { ...metadata, public_key: 'invalid-key' }),
  /数据库加密公钥无效/
)
await assert.rejects(
  cryptoModule.encryptPlantModePassword('a'.repeat(319), metadata),
  /最多支持 318 字节/
)
assert.equal(await cryptoModule.encryptPlantModePassword(encrypted, metadata), encrypted)

process.stdout.write('database credential crypto tests passed\n')
