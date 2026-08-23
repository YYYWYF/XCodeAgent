import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState
} from 'react';
import type { ApplicationLifecycle } from '../typings';
import {
  compareWorkbenchPhases,
  deriveWorkbenchExecutionPhase,
  deriveWorkbenchPhaseValidity,
  deriveWorkbenchReachedPhase,
  isObjectEditableInPhase,
  WORKBENCH_PHASE_AGENTS,
  type EditableObjectType,
  type WorkbenchAgentIdentity,
  type WorkbenchPhase,
  type WorkbenchPhaseValidity
} from '../workbenchPhase';

type WorkbenchPhaseContextValue = {
  /** 当前工作流实际执行的阶段，由 lifecycle 权威推导。 */
  executionPhase: WorkbenchPhase;
  /** 当前用户查看和编辑的阶段；阶段回退只改变这里。 */
  viewingPhase: WorkbenchPhase;
  /** 当前版本已经到达的最高阶段，决定未来产物是否可见。 */
  reachedPhase: WorkbenchPhase;
  /** 各阶段产物对当前版本是否仍然有效。 */
  phaseValidity: Record<WorkbenchPhase, WorkbenchPhaseValidity>;
  /** 手动查看阶段覆盖；null 表示跟随执行阶段。 */
  manualOverride: WorkbenchPhase | null;
  /** 切换查看阶段；传 null 回到「跟随旅程」。 */
  switchPhase: (phase: WorkbenchPhase | null) => void;
  /** 当前查看阶段的 Agent 身份。 */
  agent: WorkbenchAgentIdentity;
  /** 阶段门禁：某对象当前是否可编辑。 */
  canEdit: (objectType: EditableObjectType) => boolean;
  /** 已生成版本锁死后，阶段只展示旅程位置，不再承担 Agent 调度。 */
  locked: boolean;
};

const WorkbenchPhaseContext = createContext<WorkbenchPhaseContextValue | null>(null);

/**
 * 按应用隔离的手动查看阶段覆盖。旅程向前自动推进 executionPhase；
 * 用户手动切回上游阶段会覆盖 viewingPhase，传 null 恢复跟随旅程。
 */
export function WorkbenchPhaseProvider({
  applicationId,
  lifecycle,
  locked = false,
  children
}: {
  applicationId: string;
  lifecycle?: ApplicationLifecycle;
  locked?: boolean;
  children: ReactNode;
}): JSX.Element {
  const executionPhase = deriveWorkbenchExecutionPhase(lifecycle);
  const derivedReachedPhase = deriveWorkbenchReachedPhase(lifecycle);
  // 多应用切换时各自保留独立的覆盖值，避免互相串用。
  const [overrides, setOverrides] = useState<Record<string, WorkbenchPhase | null>>({});
  // 需求/计划回退会产生新的生命周期快照，但不能抹掉本版本此前已经到达的阶段。
  const [rememberedReachedPhases, setRememberedReachedPhases] = useState<
    Record<string, WorkbenchPhase>
  >({});
  const rememberedReachedPhase = rememberedReachedPhases[applicationId];
  const reachedPhase =
    rememberedReachedPhase &&
    compareWorkbenchPhases(rememberedReachedPhase, derivedReachedPhase) > 0
      ? rememberedReachedPhase
      : derivedReachedPhase;
  const phaseValidity = deriveWorkbenchPhaseValidity(lifecycle, reachedPhase);

  // 将本版本已到达的最高阶段记住，保证上游变更后仍可查看后续阶段。
  useEffect(() => {
    setRememberedReachedPhases((current) => {
      const previous = current[applicationId];
      if (previous && compareWorkbenchPhases(previous, derivedReachedPhase) >= 0) {
        return current;
      }
      return { ...current, [applicationId]: derivedReachedPhase };
    });
  }, [applicationId, derivedReachedPhase]);
  const manualOverride = overrides[applicationId] ?? null;

  const value = useMemo<WorkbenchPhaseContextValue>(() => {
    // 已生成版本只能回看其权威旅程位置，不能沿用迭代期间的手动查看阶段。
    const viewingPhase = locked
      ? executionPhase
      : manualOverride && compareWorkbenchPhases(manualOverride, reachedPhase) <= 0
        ? manualOverride
        : executionPhase;
    return {
      executionPhase,
      viewingPhase,
      reachedPhase,
      phaseValidity,
      manualOverride,
      switchPhase: (next) => {
        if (locked) return;
        setOverrides((current) => ({
          ...current,
          [applicationId]:
            next && compareWorkbenchPhases(next, reachedPhase) <= 0 ? next : null
        }));
      },
      agent: WORKBENCH_PHASE_AGENTS[viewingPhase],
      canEdit: (objectType) => !locked && isObjectEditableInPhase(objectType, viewingPhase),
      locked
    };
  }, [applicationId, executionPhase, reachedPhase, phaseValidity, manualOverride, locked]);

  return (
    <WorkbenchPhaseContext.Provider value={value}>{children}</WorkbenchPhaseContext.Provider>
  );
}

// Provider 与 Hook 共同构成一个上下文模块，保留同文件导出以避免拆散其唯一公共入口。
// eslint-disable-next-line react-refresh/only-export-components
export function useWorkbenchPhase(): WorkbenchPhaseContextValue {
  const context = useContext(WorkbenchPhaseContext);
  // 尽早暴露 Provider 遗漏问题，避免组件读到静默的默认阶段。
  if (!context) {
    throw new Error('useWorkbenchPhase must be used within WorkbenchPhaseProvider');
  }
  return context;
}
