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
  isObjectEditableInPhase,
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
  /** 已生成版本锁死后，阶段只展示旅程位置，不再承担 Agent 调度。 */
  locked: boolean;
};

const WorkbenchPhaseContext = createContext<WorkbenchPhaseContextValue | null>(null);

/**
 * 按应用隔离的手动阶段覆盖。旅程向前自动推进阶段（derivedPhase）；
 * 用户手动切回（例如切到产品做增量迭代）会覆盖该值，传 null 恢复跟随旅程。
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
  const derivedPhase = deriveWorkbenchPhase(lifecycle);
  // 多应用切换时各自保留独立的覆盖值，避免互相串用。
  const [overrides, setOverrides] = useState<Record<string, WorkbenchPhase | null>>({});
  const manualOverride = overrides[applicationId] ?? null;

  const value = useMemo<WorkbenchPhaseContextValue>(() => {
    const phase = manualOverride ?? derivedPhase;
    return {
      phase,
      derivedPhase,
      manualOverride,
      switchPhase: (next) => {
        if (locked) return;
        setOverrides((current) => ({ ...current, [applicationId]: next ?? null }));
      },
      agent: WORKBENCH_PHASE_AGENTS[phase],
      canEdit: (objectType) => !locked && isObjectEditableInPhase(objectType, phase),
      locked
    };
  }, [applicationId, manualOverride, derivedPhase, locked]);

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
