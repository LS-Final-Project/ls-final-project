import { useState, type FormEvent } from 'react';
import type { ResolutionBody, WorkflowRecord } from '../api/cs-workflow';
import { CAUSE_LABEL, DEPT_CODES, RESPONSIBILITY_LABEL, deptName, formatStamp } from '../ls-shell/cs-labels';

export type ResolutionForm = Omit<ResolutionBody, 'version'>;
const FAULT_ID = /^FLT-\d{3}$/;

// 해결 기록 입력(종결 뒤 문의당 한 건). 고장 코드는 사람이 확인한 값을 직접 적는다. 업무 상태의 반품 확정에서 복사하지 않는다.
export default function ResolutionCaseForm({ workflow, busy, onSubmit, onCancel }: {
  workflow?: WorkflowRecord;
  busy: boolean;
  onSubmit: (form: ResolutionForm) => void;
  onCancel: () => void;
}) {
  const [fault, setFault] = useState('');
  const [symptom, setSymptom] = useState('');
  const [causeType, setCauseType] = useState('');
  const [rootCause, setRootCause] = useState('');
  const [action, setAction] = useState('');
  const [prevention, setPrevention] = useState('');
  const [dept, setDept] = useState('');
  const [answer, setAnswer] = useState('');
  const [onsite, setOnsite] = useState<'' | 'yes' | 'no'>('');
  const [rma, setRma] = useState('');
  const [fwAt, setFwAt] = useState('');
  const [fwFixed, setFwFixed] = useState('');
  const [responsibility, setResponsibility] = useState('');
  const [reason, setReason] = useState('');
  // 이 문의에서 채택(확인 완료)된 부서 답변만 연결할 수 있다.
  const accepted = (workflow?.dept_results ?? []).filter(item => item.review_status === 'ACCEPTED');
  const faultOk = FAULT_ID.test(fault.trim());
  const ready = faultOk && symptom.trim() && causeType && rootCause.trim() && action.trim() && dept && onsite !== '';
  const blank = (value: string) => value.trim() || null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (busy || !ready) return;
    onSubmit({
      fault_id: fault.trim(), symptom: symptom.trim(), root_cause_type: causeType as ResolutionBody['root_cause_type'], root_cause: rootCause.trim(), action_taken: action.trim(),
      resolved_by_dept: dept as ResolutionBody['resolved_by_dept'], onsite_visit: onsite === 'yes', recurrence_prevention: blank(prevention), rma_no: blank(rma),
      fw_version_at_issue: blank(fwAt), fw_fixed_version: blank(fwFixed), dept_result_id: answer || null,
      responsibility: (responsibility || null) as ResolutionBody['responsibility'],
      non_manufacturing_reason: responsibility === 'NON_MANUFACTURING' ? blank(reason) : null,
    });
  };

  return <form onSubmit={submit} aria-label="해결 기록">
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>해결 기록은 문의당 한 건이고 등록하면 고치는 경로가 없습니다. 사람이 확인한 내용만 적으세요.</div>
    <div className="kv-field"><span className="req">고장 코드</span><div><input type="text" aria-label="고장 코드" placeholder="FLT-012" maxLength={7} value={fault} onChange={event => setFault(event.target.value)} />{fault.trim() !== '' && !faultOk && <small className="unc"> FLT-000 형식이어야 합니다</small>}</div></div>
    <div className="kv-field"><span className="req">증상</span><textarea aria-label="증상" rows={2} value={symptom} onChange={event => setSymptom(event.target.value)} /></div>
    <div className="kv-field"><span className="req">원인 유형</span>
      <select aria-label="원인 유형" value={causeType} onChange={event => setCauseType(event.target.value)}><option value="">선택</option>{Object.entries(CAUSE_LABEL).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></div>
    <div className="kv-field"><span className="req">원인</span><textarea aria-label="원인" rows={2} value={rootCause} onChange={event => setRootCause(event.target.value)} /></div>
    <div className="kv-field"><span className="req">조치</span><textarea aria-label="조치" rows={2} value={action} onChange={event => setAction(event.target.value)} /></div>
    <div className="kv-field"><span>재발 방지</span><input type="text" aria-label="재발 방지" value={prevention} onChange={event => setPrevention(event.target.value)} /></div>
    <div className="kv-field"><span>펌웨어</span><div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
      <input type="text" aria-label="문제 시점 펌웨어" placeholder="문제 시점" value={fwAt} onChange={event => setFwAt(event.target.value)} />→<input type="text" aria-label="수정 펌웨어" placeholder="수정 판본" value={fwFixed} onChange={event => setFwFixed(event.target.value)} /></div></div>
    <div className="kv-field"><span className="req">해결 부서</span>
      <select aria-label="해결 부서" value={dept} onChange={event => setDept(event.target.value)}><option value="">선택</option>{DEPT_CODES.map(code => <option key={code} value={code}>{deptName(code)}</option>)}<option value="GCS_L1">C/S 1차 (GCS_L1)</option></select></div>
    <div className="kv-field"><span>연결 답변</span>
      <select aria-label="연결 답변" value={answer} onChange={event => setAnswer(event.target.value)}><option value="">연결 안 함</option>{accepted.map(item => <option key={item.id} value={item.id}>{deptName(item.dept_code)}, {formatStamp(item.submitted_at)} (확인 완료)</option>)}</select></div>
    <div className="kv-field"><span>RMA</span><input type="text" aria-label="RMA 번호" value={rma} onChange={event => setRma(event.target.value)} /></div>
    <div className="kv-field"><span className="req">현장 방문</span>
      <select aria-label="현장 방문" value={onsite} onChange={event => setOnsite(event.target.value as '' | 'yes' | 'no')}><option value="">선택</option><option value="yes">예</option><option value="no">아니오</option></select></div>
    <div className="kv-field"><span>책임 구분</span>
      <select aria-label="책임 구분" value={responsibility} onChange={event => setResponsibility(event.target.value)}><option value="">선택 안 함</option>{Object.entries(RESPONSIBILITY_LABEL).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></div>
    {responsibility === 'NON_MANUFACTURING' && <div className="kv-field"><span>제조 외 사유</span><input type="text" aria-label="제조 외 사유" placeholder="예: 운송 손상" maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></div>}
    <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={onCancel}>취소</button><button type="submit" className="kb primary" disabled={busy || !ready}>해결 기록 저장</button></div>
  </form>;
}
