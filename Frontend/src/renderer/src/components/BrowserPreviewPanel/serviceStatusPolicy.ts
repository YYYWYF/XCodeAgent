export type PreviewServiceActionAvailability = {
  canRestart: boolean
  canDiagnose: boolean
}

/** 允许服务重启与应用任务并行，仅让诊断修复继续遵守任务占用。 */
export function previewServiceActionAvailability(input: {
  busy: boolean
  blockedReason: string
  repairAvailable?: boolean
}): PreviewServiceActionAvailability {
  const maintenanceAvailable = !input.busy && !input.blockedReason
  return {
    canRestart: !input.busy,
    canDiagnose: maintenanceAvailable && input.repairAvailable === true
  }
}
