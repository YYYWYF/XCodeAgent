/** 规划阶段入口事务的执行结果，明确区分前端交接失败和后端执行失败。 */
export type PlanningStageEntryOutcome<T> =
  | { status: 'completed'; session: T }
  | { status: 'frontend_handoff_failed'; error: unknown }
  | { status: 'backend_failed'; session: T; error: unknown }

/** 规划阶段入口事务的两段式回调，rollback 只属于前端交接阶段。 */
export type PlanningStageEntryTransaction<T> = {
  prepare: () => Promise<T>
  execute: (session: T) => Promise<void>
  rollback: (session: T | undefined, error: unknown) => Promise<void>
}

/** 执行规划阶段入口：前端交接失败可回滚，后端执行失败不得回滚已完成的交接。 */
export async function runPlanningStageEntryTransaction<T>(
  transaction: PlanningStageEntryTransaction<T>
): Promise<PlanningStageEntryOutcome<T>> {
  let session: T | undefined
  try {
    session = await transaction.prepare()
  } catch (error) {
    await transaction.rollback(session, error)
    return { status: 'frontend_handoff_failed', error }
  }

  try {
    await transaction.execute(session)
    return { status: 'completed', session }
  } catch (error) {
    // 后端已开始接管时，保留规划阶段和 StageSession，交由现有 Recovery/错误投影处理。
    return { status: 'backend_failed', session, error }
  }
}
