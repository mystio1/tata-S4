/** Root component: bootstraps state and renders the active page. */

import { useEffect } from 'react';

import { AppShell } from '@/components/AppShell';
import { Notice } from '@/components/primitives';
import { NetworkOverview } from '@/pages/NetworkOverview';
import {
  AIIntelligence,
  Experiments,
  LiveTraffic,
  Performance,
  QoSController,
  SafetyLogs,
  SettingsPage,
} from '@/pages/pages';
import { useQoSenseStore } from '@/store/useQoSenseStore';

export default function App() {
  const bootstrap = useQoSenseStore((s) => s.bootstrap);
  const disconnect = useQoSenseStore((s) => s.disconnect);
  const bootstrapped = useQoSenseStore((s) => s.bootstrapped);
  const bootstrapError = useQoSenseStore((s) => s.bootstrapError);
  const page = useQoSenseStore((s) => s.page);

  useEffect(() => {
    void bootstrap();
    return () => disconnect();
  }, [bootstrap, disconnect]);

  if (!bootstrapped) {
    return (
      <div className="empty-state" style={{ height: '100%', justifyContent: 'center' }}>
        Connecting to the QoSense API…
      </div>
    );
  }

  return (
    <AppShell>
      {bootstrapError && (
        <Notice tone="critical">
          {bootstrapError} The dashboard shows no values rather than placeholder ones. Start the
          backend with <code>uvicorn app.main:app --reload</code> and reload this page.
        </Notice>
      )}
      {page === 'overview' && <NetworkOverview />}
      {page === 'traffic' && <LiveTraffic />}
      {page === 'intelligence' && <AIIntelligence />}
      {page === 'controller' && <QoSController />}
      {page === 'experiments' && <Experiments />}
      {page === 'performance' && <Performance />}
      {page === 'safety' && <SafetyLogs />}
      {page === 'settings' && <SettingsPage />}
    </AppShell>
  );
}
