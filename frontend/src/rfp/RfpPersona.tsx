/* Adapted from Vercel AI Elements Persona, Copyright 2023 Vercel, Inc.
 * Apache-2.0 source notice: public/vendor/rive/LICENSE-ai-elements.txt.
 * Halo remains the official hosted asset; it is not copied into this repository.
 */
import { Component, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Alignment, Fit, Layout, RuntimeLoader, useRive, useStateMachineInput,
  useViewModel, useViewModelInstance, useViewModelInstanceColor,
} from '@rive-app/react-webgl2';
import './RfpPersona.css';

export type RfpPersonaState = 'idle' | 'thinking' | 'speaking';
export interface RfpPersonaProps { state: RfpPersonaState; paused: boolean; label: string }
const HALO_URL = 'https://ejiidnob33g9ap1r.public.blob.vercel-storage.com/halo-2.0.riv';
const RUNTIME = `${import.meta.env.BASE_URL}vendor/rive/2.48.0/`;
RuntimeLoader.setWasmUrl(`${RUNTIME}rive.wasm`);
RuntimeLoader.setWasmFallbackUrl(`${RUNTIME}rive_fallback.wasm`);
RuntimeLoader.setWasmThreadsUrl(null);
const STATE_MACHINE = 'default';
const layout = new Layout({ fit: Fit.Contain, alignment: Alignment.Center });

function useReducedMotion() {
  const [reduced, setReduced] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false);
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!query) return;
    const update = () => setReduced(query.matches);
    update(); query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return reduced;
}

function StaticPersona({ reason }: { reason: string }) {
  return <span className="rfp-persona-static" data-renderer="static" data-fallback-reason={reason} aria-hidden="true">AI</span>;
}

class PersonaBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <StaticPersona reason="unavailable" /> : this.props.children; }
}

/** Exactly one stable Rive instance; state changes only update its state-machine inputs. */
function Halo({ state, paused, onUnavailable }: Omit<RfpPersonaProps, 'label'> & { onUnavailable: () => void }) {
  const [ready, setReady] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // The throw-away StrictMode effect cancels this frame before any WebGL context is created.
  useEffect(() => {
    const frame = requestAnimationFrame(() => setReady(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  const onReady = useCallback(() => setLoaded(true), []);
  const enabled = ready;
  const parameters = useMemo(() => enabled ? {
    src: HALO_URL, stateMachines: STATE_MACHINE, autoplay: true, layout,
    enableRiveAssetCDN: false, automaticallyHandleEvents: false,
    shouldDisableRiveListeners: true, onLoadError: onUnavailable, onRiveReady: onReady,
  } : null, [enabled, onUnavailable, onReady]);
  const { rive, RiveComponent } = useRive(parameters);
  const listening = useStateMachineInput(rive, STATE_MACHINE, 'listening');
  const thinking = useStateMachineInput(rive, STATE_MACHINE, 'thinking');
  const speaking = useStateMachineInput(rive, STATE_MACHINE, 'speaking');
  const asleep = useStateMachineInput(rive, STATE_MACHINE, 'asleep');
  const viewModel = useViewModel(rive, { useDefault: true });
  const instance = useViewModelInstance(viewModel, { rive, useDefault: true });
  const { value: colorValue, setRgb } = useViewModelInstanceColor('color', instance);
  // The ERP surface is light regardless of the OS theme: retain the official light-theme color.
  useEffect(() => { if (colorValue !== null) setRgb(0, 0, 0); }, [colorValue, instance, setRgb]);
  useEffect(() => {
    if (listening) listening.value = false;
    if (thinking) thinking.value = state === 'thinking';
    if (speaking) speaking.value = state === 'speaking';
    if (asleep) asleep.value = false;
    if (!rive) return;
    if (paused) { rive.pause(STATE_MACHINE); rive.drawFrame(); }
    else rive.play(STATE_MACHINE);
  }, [state, paused, rive, listening, thinking, speaking, asleep]);
  useEffect(() => {
    if (!enabled || loaded) return;
    const timer = setTimeout(onUnavailable, 15_000);
    return () => clearTimeout(timer);
  }, [enabled, loaded, onUnavailable]);
  return <div className="rfp-persona-content" data-renderer={loaded ? 'rive' : 'loading'}>
    {!loaded && <StaticPersona reason="loading" />}
    <RiveComponent className="rfp-persona-canvas" aria-hidden="true" tabIndex={-1} />
  </div>;
}

export function RfpPersona({ state, paused, label }: RfpPersonaProps) {
  const reduced = useReducedMotion();
  const [failed, setFailed] = useState(false);
  const onUnavailable = useCallback(() => setFailed(true), []);
  // Unmount all Rive hooks together. Clearing only its canvas leaves binding effects
  // able to access freed WASM view-model/state-machine objects during the same update.
  return <div className="rfp-persona" role="img" aria-label={label} data-state={state} data-paused={paused}>
    {reduced || failed ? <StaticPersona reason={reduced ? 'reduced-motion' : 'unavailable'} /> : <PersonaBoundary><Halo state={state} paused={paused} onUnavailable={onUnavailable} /></PersonaBoundary>}
  </div>;
}
