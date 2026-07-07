import { useState } from 'react';
import type { ApplicationConfig } from '../typings';
import WelcomePage from './WelcomePage';
import WorkbenchPage from './WorkbenchPage';

export default function AppEntryPage() {
  const [activeApplication, setActiveApplication] = useState<ApplicationConfig | null>(null);

  const handleReturnWelcome = () => {
    setActiveApplication(null);
  };

  if (!activeApplication) {
    return <WelcomePage onOpenApplication={setActiveApplication} />;
  }

  return (
    <WorkbenchPage
      application={activeApplication}
      onReturnWelcome={handleReturnWelcome}
    />
  );
}
