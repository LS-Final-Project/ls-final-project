import { useState, type FormEvent } from 'react';
import type { DeptResultRow } from '../api/cs-workflow';
import { deptName, formatStamp } from '../ls-shell/cs-labels';

export type ReviewForm = { decision: 'ACCEPT' | 'REWORK'; note: string | null; result_version: number };

// 검토 답변 확인(보드 CS_K_Process_Review). 부서가 보낸 답변을 수용하거나 같은 부서에 다시 요청한다.
// 다시 요청은 메모가 필수다. 확인하기 전에는 부서 검토 요청과 종결을 막는다(문의 처리 화면이 처리한다).
export default function DeptResultReview({ result, round, busy, blocked, onSubmit }: {
  result: DeptResultRow;
  round: number | null;
  busy: boolean;
  blocked?: string;
  onSubmit: (form: ReviewForm) => void;
}) {
  const [decision, setDecision] = useState<'ACCEPT' | 'REWORK'>('ACCEPT');
  const [note, setNote] = useState('');
  const needNote = decision === 'REWORK';
  const ready = !needNote || note.trim() !== '';
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (busy || blocked || !ready) return;
    onSubmit({ decision, note: note.trim() || null, result_version: result.version });
  };
  return <section className="kv-box" aria-label="검토 답변 확인">
    <h2>검토 답변 확인, {deptName(result.dept_code)}{round !== null ? ` ${round}차` : ''}</h2>
    <form className="body" onSubmit={submit}>
      <div className="kv-field"><span>답변</span><div className="kv-text" style={{ minHeight: 0 }}>{result.result}</div></div>
      <div className="kv-field"><span>고객 회신 문안</span><div className="kv-text" style={{ minHeight: 0 }}>{result.reply_content ?? '문안 없음'}</div></div>
      <div className="kv-field"><span>제출</span><span className="ro">{formatStamp(result.submitted_at)}</span></div>
      <div className="kv-field"><span className="req">확인</span>
        <div className="kv-check">
          <label><input type="radio" name="review" checked={decision === 'ACCEPT'} onChange={() => setDecision('ACCEPT')} />확인 완료</label>
          <label><input type="radio" name="review" checked={decision === 'REWORK'} onChange={() => setDecision('REWORK')} />다시 요청</label>
        </div></div>
      <div className="kv-field"><span className={needNote ? 'req' : undefined}>메모</span><textarea aria-label="메모" rows={2} value={note} onChange={event => setNote(event.target.value)} /></div>
      <div className="kv-actions"><button type="submit" className="kb primary" disabled={busy || blocked !== undefined || !ready} title={blocked}>검토 저장</button></div>
      {blocked && <div className="kv-sub" style={{ margin: '4px 0 0' }}>{blocked}</div>}
      {needNote && note.trim() === '' && <div className="kv-sub" style={{ margin: '4px 0 0' }}>다시 요청은 메모에 보완할 내용을 적어야 합니다.</div>}
    </form>
  </section>;
}
