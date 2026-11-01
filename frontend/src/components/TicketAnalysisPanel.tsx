import { useState, type FormEvent } from 'react';
import type { TicketAnalysisResponse } from '../api/cs';
import type { ReturnBody, WorkflowRecord } from '../api/cs-workflow';
import { CAUSE_LABEL, RESPONSIBILITY_LABEL, RETURN_STATED_LABEL, categoryName, deptName, formatStamp } from '../ls-shell/cs-labels';

type Analysis = TicketAnalysisResponse['data'];
export type ReturnForm = Omit<ReturnBody, 'version'>;

const FAIL_NOTE = '분석 결과를 확인할 수 없습니다. 원문 정보와 구분해 확인하세요.';
const text = (value: unknown) => value === null || value === undefined || value === '' ? '-' : typeof value === 'boolean' ? (value ? '예' : '아니오') : String(value);

// AI 분류 패널. 분석 API(KAN-107)의 유효값(사람 정정이 있으면 그 값)과 규칙 결과를 읽기 전용으로 보인다.
// 반품 문의는 검사 뒤 사람이 기록하는 확정 입력(KAN-198)을 함께 보인다. AI 값으로 확정 칸을 채우지 않는다.
export default function TicketAnalysisPanel({ analysis, pending, failed = false, workflow, ticketState, canWrite, busy, onConfirm }: {
  analysis?: Analysis;
  pending: boolean;
  failed?: boolean;
  workflow?: WorkflowRecord;
  ticketState?: string;
  canWrite: boolean;
  busy: boolean;
  onConfirm: (form: ReturnForm) => void;
}) {
  const [responsibility, setResponsibility] = useState<ReturnBody['responsibility'] | ''>('');
  const [cause, setCause] = useState('');
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');
  const fields = analysis && typeof analysis.fields === 'object' && analysis.fields ? analysis.fields : undefined;
  const view = (key: string) => fields?.[key];
  const tag = (key: string) => view(key)?.corrected ? '사람 정정' : view(key)?.source === 'ai_draft' ? 'AI' : '';
  const rule = analysis?.rules?.effective ?? undefined;
  const category = view('category_code')?.effective_value;
  const isReturn = category === 'RETURN';
  const provided = view('provided_info')?.effective_value;
  const run = analysis?.latest_run ?? undefined;
  const recentDraft = analysis?.drafts?.at(-1);

  if (!fields) {
    // 분석을 읽지 못했거나(연결 전·권한) 아직 없으면 이전과 같이 자리만 둔다.
    return <section className="kv-box ai-pre">
      <h2>AI 분류<span className="unc" style={{ marginLeft: 8 }}>{pending ? '불러오는 중' : failed ? '분석 조회 실패' : '아직 분석 결과가 없습니다'}</span></h2>
      <div className="body">
        {['주분류', '보조분류', '긴급 여부', '반품 유형'].map(label => <div key={label} className="kv-field"><span>{label}</span><select aria-label={label} disabled><option>-</option></select></div>)}
        <div className="kv-sub" style={{ margin: '8px 0 0' }}>{FAIL_NOTE}</div>
      </div>
    </section>;
  }

  const confirmed = workflow?.return_responsibility ?? null;
  const open = isReturn && (ticketState === 'IN_REVIEW' || ticketState === 'ANSWERED');
  const blocked = !canWrite ? '실제 API 개발 모드에서만 실행할 수 있습니다' : !workflow ? '업무 판본을 읽는 중입니다' : !open ? '검토중·답변 완료 상태에서만 확정할 수 있습니다' : undefined;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (blocked || busy || !responsibility || !note.trim()) return;
    onConfirm({ responsibility, cause_type: (cause || null) as ReturnBody['cause_type'], non_manufacturing_reason: responsibility === 'NON_MANUFACTURING' && reason.trim() ? reason.trim() : null, response_note: note.trim() });
  };

  return <section className="kv-box ai-pre">
    <h2>AI 분류{run && <span className="auto">AI 작성 {formatStamp(run.created_at)}</span>}{!run && <span className="unc" style={{ marginLeft: 8 }}>분석 실행 없음</span>}</h2>
    <div className="body">
      {failed && <p role="alert">최신 분석 조회에 실패했습니다. 표시된 이전 결과와 구분해 확인하세요.</p>}
      <div className="kv-field"><span>주분류</span><span className="ro">{categoryName(category)}{tag('category_code') && <small className="auto"> {tag('category_code')}</small>}</span></div>
      <div className="kv-field"><span>보조분류</span><span className="ro">{text(view('secondary_category')?.effective_value)}{tag('secondary_category') && <small className="auto"> {tag('secondary_category')}</small>}</span></div>
      <div className="kv-field"><span>긴급 여부</span><span className="ro">{text(view('urgency_flag')?.effective_value)}{tag('urgency_flag') && <small className="auto"> {tag('urgency_flag')}</small>}</span></div>
      <div className="kv-field"><span>고객이 보낸 것</span><span className="ro">{Array.isArray(provided) && provided.length ? provided.join(', ') : '-'}</span></div>
      {isReturn && <div className="kv-field"><span>반품 사유(고객 주장)</span><span className="ro">{analysis?.return_subtype_stated ? RETURN_STATED_LABEL[analysis.return_subtype_stated] : '-'} <small className="auto">접수 때 밝힌 값, 확정 아님</small></span></div>}
      {rule && <>
        <div className="kv-field"><span>담당 부서</span><span className="ro">{deptName(rule.dept)}</span></div>
        <div className="kv-field"><span>우선순위</span><span className="ro">{rule.priority}</span></div>
        <div className="kv-field"><span>처리 기한</span><span className="ro">첫 응답 {formatStamp(analysis?.rules?.first_response_due?.effective ?? rule.first_response_due)}, 해결 {formatStamp(analysis?.rules?.resolution_due?.effective ?? rule.resolution_due)}</span></div>
      </>}
      {!run && <p>아직 분석을 실행하지 않았습니다.</p>}
      {run?.outcome && run.outcome !== 'VALID' && <p role="alert">최근 분류 시도는 유효하지 않습니다 ({run.outcome}). 기존 유효 결과와 구분해 확인하세요.</p>}
      {recentDraft && recentDraft.outcome !== 'VALID' && <p role="alert">최근 초안 작성 시도는 유효하지 않습니다 ({recentDraft.outcome}). 이전 유효 초안과 구분해 확인하세요.</p>}
      {run?.outcome === 'VALID' && !analysis?.latest_valid_draft_id && <p role="alert">분류 결과는 있지만 유효한 초안이 없습니다. 초안 작성 결과를 확인하세요.</p>}
      {analysis?.drafts?.some(draft => draft.id === analysis.latest_valid_draft_id && draft.verification_status !== 'VERIFIED') && <p>초안 문장 검증 결과를 확인하세요. 분석 작업 완료는 모든 문장의 근거 확인을 뜻하지 않습니다.</p>}
      <div className="kv-sub" style={{ margin: '8px 0 0' }}>AI 분류는 확정값이 아니라 제안입니다. 사람이 정정한 값은 사람 정정으로 표시합니다. 정정은 위쪽 기타 메뉴의 "분류 정정"에서 합니다.</div>

      {isReturn && <form onSubmit={submit} aria-label="반품 확정" style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--erp-border-soft)' }}>
        <div className="kv-sub" style={{ margin: '0 0 6px', color: 'var(--erp-text)' }}>반품 확정 (검사 뒤 사람이 기록)</div>
        {confirmed && <div className="kv-sub" style={{ margin: '0 0 6px', color: 'var(--erp-text)' }} role="status">
          확정: {RESPONSIBILITY_LABEL[confirmed] ?? confirmed}
          {workflow?.return_cause_type ? `, 원인 유형 ${CAUSE_LABEL[workflow.return_cause_type] ?? workflow.return_cause_type}` : ''}
          {workflow?.return_non_manufacturing_reason ? `, 제조 외 사유 ${workflow.return_non_manufacturing_reason}` : ''}
          . 대응 내용 {workflow?.return_response_note ?? '-'}. 확정자 {workflow?.return_subtype_confirmed_by ?? '-'}, {workflow?.return_subtype_confirmed_at ? formatStamp(workflow.return_subtype_confirmed_at) : '-'}
          {confirmed === 'UNDETERMINED' ? ' (불량 여부는 정해지지 않음)' : ''}. 다시 확정하면 모든 칸을 덮어씁니다.
        </div>}
        {!confirmed && <div className="kv-sub" style={{ margin: '0 0 6px' }}>아직 확정하지 않았습니다. 위 "고객 주장"은 확정처럼 쓰지 않습니다.</div>}
        <div className="kv-field"><span className="req">책임 구분</span>
          <select aria-label="책임 구분" value={responsibility} onChange={event => setResponsibility(event.target.value as ReturnBody['responsibility'] | '')}>
            <option value="">선택</option>{Object.entries(RESPONSIBILITY_LABEL).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></div>
        <div className="kv-field"><span>원인 유형</span>
          <select aria-label="원인 유형" value={cause} onChange={event => setCause(event.target.value)}>
            <option value="">선택 안 함</option>{Object.entries(CAUSE_LABEL).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></div>
        {responsibility === 'NON_MANUFACTURING' && <div className="kv-field"><span>제조 외 사유</span><input type="text" aria-label="제조 외 사유" placeholder="예: 운송 손상" maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></div>}
        <div className="kv-field"><span className="req">대응 내용</span><textarea aria-label="대응 내용" rows={3} value={note} onChange={event => setNote(event.target.value)} /></div>
        <div className="kv-actions"><button type="submit" className="kb primary" disabled={busy || blocked !== undefined || !responsibility || !note.trim()} title={blocked}>{confirmed ? '다시 확정' : '반품 확정'}</button></div>
        {blocked && <div className="kv-sub" style={{ margin: '4px 0 0' }}>{blocked}</div>}
      </form>}
    </div>
  </section>;
}
