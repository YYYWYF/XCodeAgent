export type TargetType = 'frontend' | 'backend' | 'fullstack';
export type ContractStatus = 'draft' | 'ready' | 'executing' | 'verifying' | 'done' | 'blocked';
export type TaskExecutionMode = 'main-integrated' | 'subagent-plan-only' | 'subagent-direct-write';
export type AgentTaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'blocked';

export type DataModelContract = {
  name: string;
  description: string;
};

export type ApiContract = {
  name: string;
  method: string;
  path: string;
  purpose: string;
  featureId?: string | null;
};

export type DevelopmentContract = {
  id: string;
  requirement: string;
  title: string;
  targetType: TargetType;
  status: ContractStatus;
  summary: string;
  sdd: {
    spec: {
      goal: string;
      users: string[];
      scopeIn: string[];
      scopeOut: string[];
      acceptanceCriteria: string[];
    };
    design: {
      features: string[];
      sharedCapabilities: string[];
      apiConventions: string[];
      dataModels: DataModelContract[];
      permissions: string[];
      errorHandling: string[];
    };
  };
  features: FeatureContract[];
  apiContracts: ApiContract[];
  dataModels: DataModelContract[];
  taskGraph: TaskGraph;
  verificationPlan: VerificationPlan;
  risks: string[];
  openQuestions: string[];
  nextActions: string[];
};

export type FeatureContract = {
  id: string;
  name: string;
  userGoal: string;
  ui: {
    pages: string[];
    states: string[];
    interactions: string[];
  };
  apis: ApiContract[];
  dataModels: string[];
  dependencies: string[];
  acceptanceCriteria: string[];
  verification: string[];
};

export type AgentTask = {
  id: string;
  title: string;
  type: 'inspect' | 'shared' | 'feature' | 'frontend' | 'backend' | 'fullstack' | 'test' | 'verify';
  featureId?: string | null;
  assignedAgent: string;
  dependsOn: string[];
  targetFiles: string[];
  canRunInParallel: boolean;
  executionMode: TaskExecutionMode;
  status: AgentTaskStatus;
  acceptanceCriteria: string[];
  verificationCommands: string[];
  directWriteReason: string;
};

export type TaskGraph = {
  tasks: AgentTask[];
  parallelismRules: string[];
};

export type ExecutionBatch = {
  index: number;
  mode: 'serial' | 'parallel' | 'blocked';
  tasks: string[];
  reason: string;
};

export type VerificationPlan = {
  commands: string[];
  checks: string[];
};

export type OrchestrationRunArtifacts = {
  contract?: string;
  taskGraph?: string;
  events?: string;
  verification?: string;
  summary?: string;
  subagents?: string;
};

export type OrchestrationRun = {
  runId?: string | null;
  runPath?: string;
  artifacts?: OrchestrationRunArtifacts;
  retention?: Record<string, unknown>;
  message?: string;
};

export type DevelopmentOrchestrationPayload = {
  tool: 'development_orchestrator';
  status: 'questions' | 'ready' | 'executing' | 'passed' | 'failed' | 'partial' | 'not_run';
  phase: 'clarifying' | 'dispatch' | 'executing' | 'verifying';
  message: string;
  questions: unknown[];
  plan?: DevelopmentContract;
  contract?: DevelopmentContract;
  runId?: string | null;
  run?: OrchestrationRun;
  taskGraph?: TaskGraph;
  executionBatches?: ExecutionBatch[];
  verification?: Record<string, unknown>;
  state?: Record<string, unknown>;
};
