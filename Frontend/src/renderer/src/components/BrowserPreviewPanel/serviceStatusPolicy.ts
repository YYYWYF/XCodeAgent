export type PreviewServiceActionAvailability = {
  canRestart: boolean
  canDiagnose: boolean
}

/** 根据真实占用与执行状态决定维护操作，初始快照尚未返回时仍允许提交重启。 */
export function previewServiceActionAvailability(input: {
  busy: boolean
  blockedReason: string
  repairAvailable?: boolean
}): PreviewServiceActionAvailability {
  const maintenanceAvailable = !input.busy && !input.blockedReason
  return {
    canRestart: maintenanceAvailable,
    canDiagnose: maintenanceAvailable && input.repairAvailable === true
  }
}
