import type { DevelopmentContract } from './developmentContract';

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

export type RequirementDevelopmentPlan = DevelopmentContract;
