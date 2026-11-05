import { useState, type FormEvent } from 'react';
import { request } from '../api/client';
import type { ResolutionBody, WorkflowRecord } from '../api/cs-workflow';
import { CAUSE_LABEL, DEPT_CODES, RESPONSIBILITY_LABEL, deptName, formatStamp } from '../ls-shell/cs-labels';

export type ResolutionForm = Omit<ResolutionBody, 'version'>;
// 이미 있는 데이터로 미리 채울 값과 그 출처. 담당자가 확인·수정한 뒤 저장한다(AI 생성 아님).
// GET /api/v1/cs/faults(제공 고장 목록, ls-fault-catalog 브랜치). 목록 값은 그 고장의 일반 설명이지 이 문의의 확정 원인이 아니다.
export type FaultEntry = { fault_id: string; error_code: string | null; symptom: string; root_cause: string; diagnosis: string; action: string; needs_onsite: boolean; needs_rma: boolean; fw_fix_version: string | null; replacement_part: string | null };
export const listFaults = (product: string, signal?: AbortSignal) => request<{ data: { items: FaultEntry[] } }>(`/api/v1/cs/faults?product_code=${encodeURIComponent(product)}`, { signal });
export type ResolutionPrefill = { symptom?: string; action?: string; dept?: string; answer?: string; fwAt?: string; sources: Record<string, string> };
const FAULT_ID = /^FLT-\d{3}$/;

// 해결 기록 입력(종결 뒤 문의당 한 건). 고장 코드는 사람이 확인한 값을 직접 적는다. 업무 상태의 반품 확정에서 복사하지 않는다.
export default function ResolutionCaseForm({ workflow, busy, onSubmit, onCancel, prefill, faults }: {
  workflow?: WorkflowRecord;
  // 이 문의 제품의 고장 목록. 없으면(undefined) 예전처럼 코드를 직접 적는다.
  faults?: { items?: FaultEntry[]; pending?: boolean; failed?: boolean };
  prefill?: ResolutionPrefill;
  busy: boolean;
  onSubmit: (form: ResolutionForm) => void;
  onCancel: () => void;
}) {
  const [fault, setFault] = useState('');
  const [symptom, setSymptom] = useState(prefill?.symptom ?? '');
  const [causeType, setCauseType] = useState('');
  const [rootCause, setRootCause] = useState('');
  const [action, setAction] = useState(prefill?.action ?? '');
  const [prevention, setPrevention] = useState('');
  const [dept, setDept] = useState(prefill?.dept ?? '');
  const [answer, setAnswer] = useState(prefill?.answer ?? '');
  const [onsite, setOnsite] = useState<'' | 'yes' | 'no'>('');
  const [rma, setRma] = useState('');
  const [fwAt, setFwAt] = useState(prefill?.fwAt ?? '');
  const [fwFixed, setFwFixed] = useState('');
  const [responsibility, setResponsibility] = useState('');
  const [reason, setReason] = useState('');
  // 이 문의에서 채택(확인 완료)된 부서 답변만 연결할 수 있다.
  const accepted = (workflow?.dept_results ?? []).filter(item => item.review_status === 'ACCEPTED');
  const faultOk = FAULT_ID.test(fault.trim());
  const ready = faultOk && symptom.trim() && causeType && rootCause.trim() && action.trim() && dept && onsite !== '';
  const blank = (value: string) => value.trim() || null;
  const faultList = faults?.items ?? [];
  // 목록에 없는 새 고장도 기록할 수 있게 직접 입력으로 바꿀 수 있다.
  const [manualFault, setManualFault] = useState(false);
  const pickedFault = faultList.find(item => item.fault_id === fault.trim());
  const [catalogFilled, setCatalogFilled] = useState<string[]>([]);
  // 고른 고장의 목록 값으로 빈 칸만 채운다(이미 적은 값은 덮지 않는다). 담당자가 확인·수정한다.
  const fillFromCatalog = (item: FaultEntry) => {
    const filled: string[] = [];
    if (!rootCause.trim()) { setRootCause(item.root_cause); filled.push('rootCause'); }
    if (!action.trim()) { setAction(item.action); filled.push('action'); }
    if (onsite === '') { setOnsite(item.needs_onsite ? 'yes' : 'no'); filled.push('onsite'); }
    if (!fwFixed.trim() && item.fw_fix_version) { setFwFixed(item.fw_fix_version); filled.push('fwFixed'); }
    setCatalogFilled(filled);
  };
  const fromCatalog = (key: string) => catalogFilled.includes(key) ? <small className="filled">고장 목록 값 · 이 문의에 맞는지 확인</small> : null;
  const from = (key: string) => prefill?.sources[key] ? <small className="filled">자동 채움 · {prefill.sources[key]}</small> : null;

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
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>해결 기록은 문의당 한 건이고 등록하면 고치는 경로가 없습니다. 사람이 확인한 내용만 적으세요.{prefill && Object.keys(prefill.sources).length > 0 && ' 이미 있는 데이터로 일부 칸을 채웠습니다. 저장 전에 확인하고 고치세요.'}</div>
    <div className="kv-field"><span className="req">고장 코드</span><div>
      {faultList.length > 0 && !manualFault
        ? <select aria-label="고장 코드" value={pickedFault ? fault.trim() : ''} onChange={event => { if (event.target.value === '__manual__') { setManualFault(true); setFault(''); } else setFault(event.target.value); setCatalogFilled([]); }}>
          <option value="">이 제품의 고장 {faultList.length}종에서 선택</option>
          {faultList.map(item => <option key={item.fault_id} value={item.fault_id}>{item.fault_id}{item.error_code ? ` · ${item.error_code}` : ''} · {item.symptom}</option>)}
          <option value="__manual__">목록에 없음 · 코드 직접 입력</option>
        </select>
        : <input type="text" aria-label="고장 코드" placeholder="FLT-012" maxLength={7} value={fault} onChange={event => setFault(event.target.value)} />}
      {faultList.length > 0 && manualFault && <button type="button" className="kb" style={{ marginTop: 4, height: 22 }} onClick={() => { setManualFault(false); setFault(''); }}>목록에서 고르기</button>}
      {faults?.pending && <small className="unc"> 고장 목록을 읽는 중</small>}
      {faults?.failed && <small className="unc"> 고장 목록을 읽지 못했습니다. 코드를 직접 적으세요(FLT-000)</small>}
      {faults?.items && faultList.length === 0 && <small className="unc"> 이 제품은 고장 목록에 없습니다. 코드를 직접 적으세요</small>}
      {fault.trim() !== '' && !faultOk && <small className="unc"> FLT-000 형식이어야 합니다</small>}
      {pickedFault && <div className="fault-ref">
        <div><b>목록의 일반 원인</b> {pickedFault.root_cause}</div>
        <div><b>진단</b> {pickedFault.diagnosis}</div>
        <div><b>일반 조치</b> {pickedFault.action}</div>
        <div className="tags"><span>{pickedFault.needs_onsite ? '현장 출동 필요' : '원격 가능'}</span><span>{pickedFault.needs_rma ? 'RMA 필요' : 'RMA 불필요'}</span>{pickedFault.fw_fix_version && <span>수정 펌웨어 {pickedFault.fw_fix_version}</span>}{pickedFault.replacement_part && <span>교체 부품 {pickedFault.replacement_part}</span>}</div>
        <button type="button" className="kb" onClick={() => fillFromCatalog(pickedFault)}>빈 칸을 목록 값으로 채우기</button>
      </div>}
    </div></div>
    <div className="kv-field"><span className="req">증상</span><div><textarea aria-label="증상" rows={2} value={symptom} onChange={event => setSymptom(event.target.value)} />{from('symptom')}</div></div>
    <div className="kv-field"><span className="req">원인 유형</span>
      <select aria-label="원인 유형" value={causeType} onChange={event => setCauseType(event.target.value)}><option value="">선택</option>{Object.entries(CAUSE_LABEL).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></div>
    <div className="kv-field"><span className="req">원인</span><div><textarea aria-label="원인" rows={2} value={rootCause} onChange={event => setRootCause(event.target.value)} />{fromCatalog('rootCause')}</div></div>
    <div className="kv-field"><span className="req">조치</span><div><textarea aria-label="조치" rows={2} value={action} onChange={event => setAction(event.target.value)} />{from('action')}{fromCatalog('action')}</div></div>
    <div className="kv-field"><span>재발 방지</span><input type="text" aria-label="재발 방지" value={prevention} onChange={event => setPrevention(event.target.value)} /></div>
    <div className="kv-field"><span>펌웨어</span><div><div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
      <input type="text" aria-label="문제 시점 펌웨어" placeholder="문제 시점" value={fwAt} onChange={event => setFwAt(event.target.value)} />→<input type="text" aria-label="수정 펌웨어" placeholder="수정 판본" value={fwFixed} onChange={event => setFwFixed(event.target.value)} /></div>{from('fwAt')}{fromCatalog('fwFixed')}</div></div>
    <div className="kv-field"><span className="req">해결 부서</span>
      <div><select aria-label="해결 부서" value={dept} onChange={event => setDept(event.target.value)}><option value="">선택</option>{DEPT_CODES.map(code => <option key={code} value={code}>{deptName(code)}</option>)}<option value="GCS_L1">C/S 1차 (GCS_L1)</option></select>{from('dept')}</div></div>
    <div className="kv-field"><span>연결 답변</span>
      <div><select aria-label="연결 답변" value={answer} onChange={event => setAnswer(event.target.value)}><option value="">연결 안 함</option>{accepted.map(item => <option key={item.id} value={item.id}>{deptName(item.dept_code)}, {formatStamp(item.submitted_at)} (확인 완료)</option>)}</select>{from('answer')}</div></div>
    <div className="kv-field"><span>RMA</span><input type="text" aria-label="RMA 번호" value={rma} onChange={event => setRma(event.target.value)} /></div>
    <div className="kv-field"><span className="req">현장 방문</span>
      <div><select aria-label="현장 방문" value={onsite} onChange={event => setOnsite(event.target.value as '' | 'yes' | 'no')}><option value="">선택</option><option value="yes">예</option><option value="no">아니오</option></select>{fromCatalog('onsite')}</div></div>
    <div className="kv-field"><span>책임 구분</span>
      <select aria-label="책임 구분" value={responsibility} onChange={event => setResponsibility(event.target.value)}><option value="">선택 안 함</option>{Object.entries(RESPONSIBILITY_LABEL).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></div>
    {responsibility === 'NON_MANUFACTURING' && <div className="kv-field"><span>제조 외 사유</span><input type="text" aria-label="제조 외 사유" placeholder="예: 운송 손상" maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></div>}
    <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={onCancel}>취소</button><button type="submit" className="kb primary" disabled={busy || !ready}>해결 기록 저장</button></div>
  </form>;
}
