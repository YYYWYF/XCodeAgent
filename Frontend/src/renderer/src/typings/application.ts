export type ApplicationAudience =
  | 'operator'
  | 'admin'
  | 'user'
  | 'customer'
  | 'developer'
  | 'other';

export type ApplicationTerminal = 'pc' | 'mobile' | 'responsive';
export type ApplicationTheme = 'light' | 'dark' | 'enterprise-blue' | 'custom';
export type ApplicationLayout =
  | 'top-nav'
  | 'side-nav'
  | 'top-side-nav'
  | 'immersive'
  | 'login-admin';

export interface ApplicationConfig {
  id: string;
  name: string;
  workspaceRoot?: string;
  projectParentPath?: string;
  projectDirectoryName?: string;
  source?: 'new' | 'existing-workspace';
  audience: ApplicationAudience;
  terminal: ApplicationTerminal;
  enableAuth: boolean;
  enableTracking: boolean;
  theme: ApplicationTheme;
  layout: ApplicationLayout;
  enableTabs: boolean;
  pages: string[];
  defaultPage: string;
  hasDynamicRoutes: boolean;
  dynamicRouteDescription?: string;
  requirementPlan?: RequirementDevelopmentPlan;
  createdAt: number;
}

export interface ApplicationDraft {
  name: string;
  projectParentPath: string;
  projectDirectoryName: string;
  audience: ApplicationAudience;
  terminal: ApplicationTerminal;
  enableAuth: boolean;
  enableTracking: boolean;
  theme: ApplicationTheme;
  layout: ApplicationLayout;
  enableTabs: boolean;
  pagesText: string;
  defaultPage?: string;
  hasDynamicRoutes: boolean;
  dynamicRouteDescription?: string;
}

export interface RequirementDevelopmentPlan {
  title: string;
  summary: string;
  assumptions: string[];
  scope: {
    in: string[];
    out: string[];
  };
  modules?: Array<{
    name: string;
    enabled: boolean;
    reason: string;
  }>;
  pages: Array<{
    name: string;
    goal: string;
    keyInteractions?: string[];
  }>;
  frontendTasks: string[];
  backendTasks: string[];
  apis: Array<{
    name: string;
    method: string;
    path: string;
    purpose: string;
  }>;
  dataModels: Array<{
    name: string;
    description: string;
  }>;
  milestones: Array<{
    name: string;
    deliverables: string[];
  }>;
  risks: string[];
  openQuestions: string[];
  nextActions: string[];
}
