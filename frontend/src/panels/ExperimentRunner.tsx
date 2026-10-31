/**
 * Scenario setup and run controls.
 *
 * Every control is wired to backend state. Pressing start changes what the
 * server is doing; it does not animate a success locally and hope the backend
 * agrees. Disabled states and errors come from the API's own responses.
 */

import { useEffect, useMemo, useState } from 'react';

import type { ExperimentMode, ScenarioDefinition } from '@/api/types';
import { Badge, EmptyState, Notice, Panel } from '@/components/primitives';
import { humanise } from '@/lib/format';
import { useQoSenseStore } from '@/store/useQoSenseStore';

const MODES: { id: ExperimentMode; label: string; blurb: string }[] = [
  {
    id: 'NO_QOS',
    label: 'Mode 1 · No QoS',
    blurb: 'Reference condition. The controller observes and never shapes.',
  },
  {
    id: 'TRADITIONAL_ADAPTIVE',
    label: 'Mode 2 · Traditional adaptive',
    blurb: 'Deterministic rule classification with the shared estimator, policy engine and verifier.',
  },
  {
    id: 'CLASSIFIER_ONLY',
    label: 'Mode 3 · Classifier only',
    blurb: 'Model 1 classification with every other component held constant.',
  },
  {
    id: 'FULL_QOSENSE',
    label: 'Mode 4 · Full QoSense',
    blurb: 'Model 1 plus Model 2 congestion prediction, everything else held constant.',
  },
];

export function ExperimentRunner({ compact = false }: { compact?: boolean }) {
  const scenarios = useQoSenseStore((s) => s.scenarios);
  const system = useQoSenseStore((s) => s.system);
  const experiment = useQoSenseStore((s) => s.experiment);
  const busy = useQoSenseStore((s) => s.busy);
  const actionError = useQoSenseStore((s) => s.actionError);
  const clearActionError = useQoSenseStore((s) => s.clearActionError);

  const createExperiment = useQoSenseStore((s) => s.createExperiment);
  const startExperiment = useQoSenseStore((s) => s.startExperiment);
  const pauseExperiment = useQoSenseStore((s) => s.pauseExperiment);
  const resumeExperiment = useQoSenseStore((s) => s.resumeExperiment);
  const stopExperiment = useQoSenseStore((s) => s.stopExperiment);
  const resetExperiment = useQoSenseStore((s) => s.resetExperiment);

  const [scenarioId, setScenarioId] = useState('iso_during_call');
  const [mode, setMode] = useState<ExperimentMode>('TRADITIONAL_ADAPTIVE');
  const [seed, setSeed] = useState(42);
  const [overrides, setOverrides] = useState<Record<string, number>>({});

  const scenario: ScenarioDefinition | undefined = useMemo(
    () => scenarios.find((s) => s.scenario_id === scenarioId),
    [scenarios, scenarioId],
  );

  // Reset parameter overrides to the chosen scenario's declared defaults.
  useEffect(() => {
    if (!scenario) return;
    setOverrides(
      Object.fromEntries(scenario.parameters.map((p) => [p.name, p.default])),
    );
  }, [scenario]);

  const availability = system?.modes.find((m) => m.mode === mode);
  const status = experiment?.status;
  const running = status === 'RUNNING';
  const paused = status === 'PAUSED';
  const finished = status === 'COMPLETED' || status === 'STOPPED';
  const created = status === 'CREATED';

  const create = () =>
    createExperiment({
      scenario_id: scenarioId,
      mode,
      data_mode: 'SIMULATION',
      seed,
      parameters: overrides,
    });

  const configureAndStart = async () => {
    const next = await createExperiment({
      scenario_id: scenarioId,
      mode,
      data_mode: 'SIMULATION',
      seed,
      parameters: overrides,
    });
    if (next) await startExperiment();
  };

  if (scenarios.length === 0) {
    return (
      <Panel title="Start experiment">
        <EmptyState
          title="No scenarios loaded."
          hint="The dashboard could not reach the QoSense API."
        />
      </Panel>
    );
  }

  return (
    <Panel
      title="Experiment"
      actions={
        experiment ? (
          <Badge tone={running ? 'success' : finished ? 'muted' : 'warning'} dot>
            {humanise(status ?? 'none')}
          </Badge>
        ) : (
          <Badge tone="muted">Not created</Badge>
        )
      }
    >
      {actionError && (
        <Notice tone="critical">
          {actionError}{' '}
          <button
            type="button"
            className="btn"
            style={{ marginLeft: 8, padding: '2px 8px' }}
            onClick={clearActionError}
          >
            Dismiss
          </button>
        </Notice>
      )}

      <label className="field">
        <span className="field__label">Scenario</span>
        <select
          value={scenarioId}
          onChange={(e) => setScenarioId(e.target.value)}
          disabled={busy || running}
        >
          {scenarios.map((s) => (
            <option key={s.scenario_id} value={s.scenario_id}>
              {s.name}
            </option>
          ))}
        </select>
        {scenario && <span className="field__hint">{scenario.summary}</span>}
      </label>

      <label className="field">
        <span className="field__label">Comparison mode</span>
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as ExperimentMode)}
          disabled={busy || running}
        >
          {MODES.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
        <span className="field__hint">{MODES.find((m) => m.id === mode)?.blurb}</span>
      </label>

      {availability?.degraded && (
        <Notice tone="warning">{availability.detail}</Notice>
      )}

      {!compact && scenario && (
        <div className="field-row">
          {scenario.parameters.map((parameter) => (
            <label className="field" key={parameter.name}>
              <span className="field__label">
                {parameter.label} ({parameter.unit})
              </span>
              <input
                type="number"
                min={parameter.minimum}
                max={parameter.maximum}
                step={(parameter.maximum - parameter.minimum) / 100 > 1 ? 1 : 0.5}
                value={overrides[parameter.name] ?? parameter.default}
                disabled={busy || running}
                onChange={(e) =>
                  setOverrides((prev) => ({
                    ...prev,
                    [parameter.name]: Number(e.target.value),
                  }))
                }
              />
              <span className="field__hint">
                {parameter.minimum} to {parameter.maximum} {parameter.unit}
              </span>
            </label>
          ))}
          <label className="field">
            <span className="field__label">Seed</span>
            <input
              type="number"
              min={0}
              value={seed}
              disabled={busy || running}
              onChange={(e) => setSeed(Number(e.target.value))}
            />
            <span className="field__hint">The same seed reproduces the same run.</span>
          </label>
        </div>
      )}

      <div className="btn-row">
        {!experiment || finished ? (
          <>
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={configureAndStart}
            >
              {busy ? 'Working…' : 'Configure and start'}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={create}>
              Create without starting
            </button>
          </>
        ) : (
          <>
            {created && (
              <button
                type="button"
                className="btn btn--primary"
                disabled={busy}
                onClick={startExperiment}
              >
                Start
              </button>
            )}
            {running && (
              <button type="button" className="btn" disabled={busy} onClick={pauseExperiment}>
                Pause
              </button>
            )}
            {paused && (
              <button
                type="button"
                className="btn btn--primary"
                disabled={busy}
                onClick={resumeExperiment}
              >
                Resume
              </button>
            )}
            <button
              type="button"
              className="btn btn--danger"
              disabled={busy}
              onClick={stopExperiment}
            >
              Stop safely
            </button>
          </>
        )}
        {experiment && (
          <button type="button" className="btn" disabled={busy} onClick={resetExperiment}>
            Reset to baseline
          </button>
        )}
      </div>

      {experiment && experiment.notes.length > 0 && (
        <Notice tone="warning">
          {experiment.notes.map((note) => (
            <div key={note}>{note}</div>
          ))}
        </Notice>
      )}

      <p className="small faint" style={{ margin: 0 }}>
        Laboratory emulation. Stopping removes any shaping and returns the emulated link to its
        baseline. All values are labelled SIMULATION.
      </p>
    </Panel>
  );
}
