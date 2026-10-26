import { useEffect, useRef, useState } from 'react';
import type { JobStatus } from '../api/evaluations';
import { prepareRunCommand, sendRunCommand, loadRunCommand, saveRunCommand, RunControlError, type RunAction, type RunCommand } from '../api/evaluation-controls';

/** 취소·보류 해제와 불명확 요청의 재확인을 표시한다. Args: 현재 스냅샷, 재조회 함수. Returns: 실행 제어 UI. */
export default function RunControls({ jobId, snapshot, onRefresh }: { jobId: string; snapshot?: JobStatus; onRefresh: () => void }) {
  const [saved] = useState(() => {
    try { return { command: loadRunCommand(jobId, sessionStorage), error: false }; }
    catch { return { command: undefined, error: true }; }
  });
  const [command, setCommand] = useState<RunCommand | undefined>(saved.command);
  const [phase, setPhase] = useState<'idle' | 'preparing' | 'confirm' | 'sending' | 'unknown'>(saved.command ? 'unknown' : 'idle');
  const [message, setMessage] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const active = !saved.error && snapshot && ['QUEUED', 'RUNNING'].includes(snapshot.run_status);

  /** 요청 전 판본과 동작을 확인한다. Args: 선택 동작. Returns: 준비 완료. */
  async function prepare(action: RunAction) {
    if (!snapshot || request.current) return;
    const controller = new AbortController(); request.current = controller;
    setPhase('preparing'); setMessage('');
    try { setCommand(await prepareRunCommand(snapshot, action, controller.signal)); setPhase('confirm'); }
    catch (error) {
      if (!controller.signal.aborted) {
        setMessage(error instanceof TypeError ? '서버에 연결하지 못했습니다.' : error instanceof Error ? error.message : '요청을 준비하지 못했습니다.');
        setPhase('idle'); onRefresh();
      }
    } finally { request.current = null; }
  }

  /** 확인한 명령을 전송하거나 그대로 재확인한다. Args: 없음. Returns: 응답 처리 완료. */
  async function submit() {
    if (!command || request.current) return;
    const wasUnknown = phase === 'unknown';
    const controller = new AbortController(); request.current = controller;
    setPhase('sending'); setMessage('');
    try {
      saveRunCommand(command, sessionStorage);
      await sendRunCommand(command, controller.signal);
      if (!controller.signal.aborted) {
        try { sessionStorage.removeItem(`evaluation-command:${command.jobId}`); }
        catch { throw new RunControlError('처리는 확인했지만 재확인 정보를 정리하지 못했습니다. 같은 요청으로 다시 확인해 주세요.', true); }
        setCommand(undefined); setPhase('idle');
        setMessage('요청 처리를 확인했습니다. 최신 실행 상태를 확인해 주세요.');
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        let unknown = wasUnknown || error instanceof RunControlError && error.uncertain;
        if (!unknown) {
          try { sessionStorage.removeItem(`evaluation-command:${command.jobId}`); }
          catch { unknown = true; }
        }
        setPhase(unknown ? 'unknown' : 'idle');
        if (!unknown) setCommand(undefined);
        setMessage(error instanceof TypeError ? '서버에 연결하지 못했습니다.' : error instanceof Error ? error.message : '요청에 실패했습니다.');
      }
    } finally {
      request.current = null;
      if (!controller.signal.aborted) onRefresh();
    }
  }
  return <section aria-labelledby="run-controls-title">
    <h2 id="run-controls-title">실행 제어</h2>
    {saved.error && <p role="alert">이 탭의 이전 요청 정보를 확인하지 못해 제어를 잠갔습니다. 저장소 설정과 이전 요청 결과를 확인해 주세요.</p>}
    <p>취소한 실행은 다시 시작할 수 없습니다. 보류 해제는 승인 조건을 다시 검사하며 실행 완료를 보장하지 않습니다.</p>
    {phase === 'idle' && <div className="accounts">
      <button disabled={!active} onClick={() => void prepare('cancel')}>실행 취소</button>
      <button disabled={!active || !snapshot?.needs_review} onClick={() => void prepare('resume')}>검토 보류 해제</button>
    </div>}
    {phase === 'preparing' && <p role="status">현재 실행 판본을 확인하고 있습니다.</p>}
    {phase === 'confirm' && <div role="group" aria-label="실행 제어 확인">
      <p>{command?.action === 'cancel' ? '이 실행을 취소할까요? 취소 후에는 재개할 수 없습니다.' : '검토 보류를 해제할까요? 현재 승인 조건을 다시 검사합니다.'}</p>
      <div className="accounts"><button onClick={() => void submit()}>확인 후 요청</button><button onClick={() => { setCommand(undefined); setPhase('idle'); }}>돌아가기</button></div>
    </div>}
    {phase === 'sending' && <p role="status">요청을 확인하고 있습니다. 중복 요청하지 마세요.</p>}
    {phase === 'unknown' && <div role="alert"><p>처리 결과가 불명확합니다. 다른 제어 요청을 만들기 전에 같은 요청으로 재확인하세요. 재확인 정보는 현재 탭에 보존됩니다.</p><button onClick={() => void submit()}>같은 요청으로 재확인</button></div>}
    {message && <p role="status">{message}</p>}
  </section>;
}
