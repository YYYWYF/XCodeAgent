import {
  createContext,
  type ReactNode,
  useContext,
  useMemo,
  useState
} from 'react';
import type { ApplicationLifecycle } from '../typings';
import {
  deriveWorkbenchPhase,
  getPersistedWorkbenchPhase,
  isObjectEditableInPhase,
  setPersistedWorkbenchPhase,
  WORKBENCH_PHASE_AGENTS,
  type EditableObjectType,
  type WorkbenchAgentIdentity,
  type WorkbenchPhase
} from '../workbenchPhase';

type WorkbenchPhaseContextValue = {
  /** 实际生效的阶段：手动覆盖优先，否则用旅程推导值。 */
  phase: WorkbenchPhase;
  /** 旅程推导的阶段（不受手动覆盖影响）。 */
  derivedPhase: WorkbenchPhase;
  /** 手动覆盖；null 表示跟随旅程（自动推进）。 */
  manualOverride: WorkbenchPhase | null;
  /** 切换阶段；传 null 回到「跟随旅程」。 */
  switchPhase: (phase: WorkbenchPhase | null) => void;
  /** 当前生效阶段的 Agent 身份。 */
  agent: WorkbenchAgentIdentity;
  /** 阶段门禁：某对象当前是否可编辑。 */
  canEdit: (objectType: EditableObjectType) => boolean;
};

const WorkbenchPhaseContext = createContext<WorkbenchPhaseContextValue | null>(null);

/**
 * 按应用隔离的手动阶段覆盖。旅程向前自动推进阶段（derivedPhase）；
 * 用户手动切回（例如切到产品做增量迭代）会覆盖该值，传 null 恢复跟随旅程。
 */
export function WorkbenchPhaseProvider({
  applicationId,
  lifecycle,
  children
}: {
  applicationId: string;
  lifecycle?: ApplicationLifecycle;
  children: ReactNode;
}): JSX.Element {
  const derivedPhase = deriveWorkbenchPhase(lifecycle);
  // 恢复用户上次手动选择的阶段；未覆盖时始终跟随后端生命周期。
  const [overrides, setOverrides] = useState<Record<string, WorkbenchPhase | null>>(() => {
    const persistedPhase = getPersistedWorkbenchPhase(applicationId);
    return persistedPhase ? { [applicationId]: persistedPhase } : {};
  });
  const manualOverride = overrides[applicationId] ?? null;

  const value = useMemo<WorkbenchPhaseContextValue>(() => {
    const phase = manualOverride ?? derivedPhase;
    return {
      phase,
      derivedPhase,
      manualOverride,
      switchPhase: (next) => {
        // 只持久化用户明确的界面覆盖；传 null 表示恢复生命周期自动阶段。
        setPersistedWorkbenchPhase(applicationId, next);
        setOverrides((current) => ({ ...current, [applicationId]: next ?? null }));
      },
      agent: WORKBENCH_PHASE_AGENTS[phase],
      canEdit: (objectType) => isObjectEditableInPhase(objectType, phase)
    };
  }, [applicationId, manualOverride, derivedPhase]);

  return (
    <WorkbenchPhaseContext.Provider value={value}>{children}</WorkbenchPhaseContext.Provider>
  );
}

export function useWorkbenchPhase(): WorkbenchPhaseContextValue {
  const context = useContext(WorkbenchPhaseContext);
  // 尽早暴露 Provider 遗漏问题，避免组件读到静默的默认阶段。
  if (!context) {
    throw new Error('useWorkbenchPhase must be used within WorkbenchPhaseProvider');
  }
  return context;
}
