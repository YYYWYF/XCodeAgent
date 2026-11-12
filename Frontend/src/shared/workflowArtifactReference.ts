export type ArtifactReference = {
  artifactId: string
  byteLength: number
  mediaType: 'application/json'
}

/** 校验当前内容地址，禁止任意路径或超出读取预算的资料引用。 */
export function isArtifactReference(value: unknown): value is ArtifactReference {
  if (!value || typeof value !== 'object') return false
  const ref = value as ArtifactReference
  return (
    typeof ref.artifactId === 'string' &&
    /^[a-f0-9]{64}$/.test(ref.artifactId) &&
    Number.isInteger(ref.byteLength) &&
    ref.byteLength >= 0 &&
    ref.byteLength <= 16 * 1024 * 1024 &&
    ref.mediaType === 'application/json'
  )
}

/** 只接受完整且有序的引用列表，损坏时不静默丢弃日志片段。 */
export function readArtifactReferences(value: unknown): ArtifactReference[] | undefined {
  return Array.isArray(value) && value.length > 0 && value.every(isArtifactReference)
    ? value
    : undefined
}
