import {
  createContext,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
  useContext,
  useMemo,
  useState,
} from 'react';
import type { EditorMode } from '../typings';

export type AiOutputMessage = {
  id: number;
  content: string;
  createdAt: number;
};

export type EditorResponse = {
  messageId: number;
  content: string;
};

type AiMessagesByEditor = Record<EditorMode, AiOutputMessage[]>;
type EditorResponses = Partial<Record<EditorMode, EditorResponse>>;

type WorkbenchContextValue = {
  aiMessages: AiMessagesByEditor;
  setAiMessages: Dispatch<SetStateAction<AiMessagesByEditor>>;
  publishAiMessage: (editorMode: EditorMode, content: string) => void;
  editorResponses: EditorResponses;
  setEditorResponses: Dispatch<SetStateAction<EditorResponses>>;
};

const WorkbenchContext = createContext<WorkbenchContextValue | null>(null);

export function WorkbenchProvider({ children }: { children: ReactNode }) {
  // 前端和后端分别保存消息，切换编辑器时不会看到另一侧的会话内容。
  const [aiMessages, setAiMessages] = useState<AiMessagesByEditor>({
    frontend: [],
    backend: [],
  });
  // 编辑器响应同样按作用域隔离，便于后续接入不同的处理器。
  const [editorResponses, setEditorResponses] = useState<EditorResponses>({});

  const value = useMemo<WorkbenchContextValue>(
    () => ({
      aiMessages,
      setAiMessages,
      publishAiMessage: (editorMode, content) => {
        const normalizedContent = content.trim();
        if (!normalizedContent) return;

        // 只更新目标编辑器的消息数组，其余作用域保持不变。
        setAiMessages((messages) => ({
          ...messages,
          [editorMode]: [
            ...messages[editorMode],
            {
              id: Date.now(),
              content: normalizedContent,
              createdAt: Date.now(),
            },
          ],
        }));
      },
      editorResponses,
      setEditorResponses,
    }),
    [aiMessages, editorResponses],
  );

  return <WorkbenchContext.Provider value={value}>{children}</WorkbenchContext.Provider>;
}

export function useWorkbench() {
  const context = useContext(WorkbenchContext);

  // 尽早暴露 Provider 遗漏问题，避免组件读取到静默的空状态。
  if (!context) {
    throw new Error('useWorkbench must be used within WorkbenchProvider');
  }

  return context;
}
