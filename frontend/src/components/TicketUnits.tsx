import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { isUnitAlreadyConfirmed, isUnitLinkConflict, isUnitMismatch, saveUnitLink, type UnitLinkBody } from '../api/cs-workflow';
import { getTicketUnits, getUnitHistory, type TicketUnitsResponse } from '../api/cs';
import { KvGrid, type KvColumn } from '../ls-shell/kv';
import { ErrorState, Loading } from './states';

type UnitRow = TicketUnitsResponse['data']['items'][number];
const LINK_LABEL: Record<string, string> = { CONFIRMED: '확정', REJECTED: '제외' };

type Status = UnitLinkBody['link_status'];
type Basis = UnitLinkBody['link_basis'];
type Repeat = NonNullable<UnitLinkBody['repeat_type']>;
const STATUS_OPTIONS: [Status, string][] = [['CANDIDATE', '확인 중'], ['CONFIRMED', '맞음'], ['REJECTED', '아님']];
const BASIS_OPTIONS: [Basis, string][] = [['SERIAL_PROVIDED', '시리얼로 확인'], ['CUSTOMER_CONFIRMED', '고객이 확인'], ['AGENT_REVIEW', '담당자 판단']];
const REPEAT_OPTIONS: [Repeat, string][] = [['SAME_UNIT', '같은 장비 재발'], ['REPLACEMENT', '교체된 장비'], ['MULTIPLE_UNITS', '여러 대'], ['CONFLICT', '기록이 안 맞음'], ['DIFFERENT_UNIT', '다른 장비']];

// 설치 장비 확인 패널의 본문. 장비 후보와 확정 장비의 이전 문의를 읽어 보이고, canWrite이면 고른 장비의 연결(이 장비인가·확인 방법·
// 이전 문의와 관계)을 PUT으로 기록한다. 패널 틀(제목 줄)은 문의 처리 화면이 그린다.
export default function TicketUnits({ ticketId, canWrite = false }: { ticketId: string; canWrite?: boolean }) {
  const [opened, setOpened] = useState(false);
  const [pickedId, setPickedId] = useState('');
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const inFlight = useRef(false);
  const mode = getApiMode();
  const enabled = import.meta.env.DEV && opened;
  const units = useQuery({ queryKey: ['ticket-units', mode, ticketId], queryFn: ({ signal }) => getTicketUnits(ticketId, signal), enabled, staleTime: 0 });
  const history = useQuery({ queryKey: ['unit-history', mode, ticketId], queryFn: ({ signal }) => getUnitHistory(ticketId, signal), enabled, staleTime: 0 });
  if (!import.meta.env.DEV) return <div className="kv-sub" style={{ margin: 0 }}>장비 조회의 운영 연결을 준비 중입니다.</div>;
  const failed = units.isError || history.isError;
  const busy = units.isFetching || history.isFetching;
  const refresh = () => { setOpened(true); if (opened) { void units.refetch(); void history.refetch(); } };

  const rows = units.data?.data.items ?? [];
  const picked = rows.find(row => row.unit.id === pickedId) ?? rows[0];
  async function save(form: FormData) {
    if (!picked || inFlight.current) return;
    const status = String(form.get('status')) as Status;
    const repeat = String(form.get('repeat') ?? '');
    const body: UnitLinkBody = { link_status: status, link_basis: String(form.get('basis')) as Basis, repeat_type: repeat === '' ? null : repeat as Repeat, version: picked.link?.version ?? null };
    inFlight.current = true; setSaving(true); setNotice(null);
    try {
      await saveUnitLink(ticketId, picked.unit.id, body, new AbortController().signal);
      setNotice({ kind: 'ok', text: '장비 연결을 저장했습니다.' });
    } catch (error) {
      setNotice({ kind: 'error', text: isUnitLinkConflict(error) ? '다른 사용자가 먼저 이 장비 연결을 바꿨습니다. 최신 내용을 다시 읽었으니 확인한 뒤 다시 저장하세요.'
        : isUnitAlreadyConfirmed(error) ? '이 문의에는 이미 다른 장비가 확정돼 있습니다. 먼저 그 장비를 "아님"으로 바꾸세요.'
        : isUnitMismatch(error) ? '고객 또는 제품이 문의와 다른 장비는 확인 중·맞음으로 둘 수 없습니다. "아님"만 기록할 수 있습니다.'
        : error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408 ? error.message
          : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 최신 내용을 읽었습니다. 반영 여부를 확인하세요.' });
    } finally {
      inFlight.current = false; setSaving(false);
      await Promise.allSettled([units.refetch(), history.refetch()]);
    }
  }

  const columns: KvColumn<UnitRow>[] = [
    ...(canWrite ? [{ key: 'pick', header: '', width: 30, className: 'c', cell: ({ unit }: UnitRow) => <input type="radio" name="unit" aria-label={`${unit.serial_no} 선택`} checked={unit.id === picked?.unit.id} onChange={() => { setPickedId(unit.id); setNotice(null); }} /> } satisfies KvColumn<UnitRow>] : []),
    { key: 'serial', header: '시리얼', headClass: '', cell: ({ unit }) => <><span>{unit.serial_no}</span>
      <small style={{ display: 'block', color: 'var(--erp-muted)', whiteSpace: 'normal' }}>설치 {unit.commissioning_date ?? '미정'} ({unit.commissioning_status}), 출하 {unit.ship_date ?? '미정'}, 최근 FW {unit.fw_version_last ?? '미정'}, 고객 {unit.customer_external_id}, 출처 {unit.data_origin} {unit.derivation_kind}</small></> },
    { key: 'product', header: '제품', width: 96, headClass: '', cell: ({ unit }) => unit.product_code },
    { key: 'match', header: '고객/제품 일치', width: 64, className: 'c', cell: ({ matches_ticket }) => matches_ticket ? '예' : '아니오' },
    { key: 'link', header: '연결', width: 48, className: 'c', cell: ({ link }) => LINK_LABEL[link?.link_status ?? ''] ?? '후보' },
  ];

  return <div>
    <div className="kv-sub" style={{ margin: '0 0 6px' }}>후보 장비는 확정 장비가 아닙니다. 이전 문의는 확정된 장비 또는 교체 전 장비의 이력만 표시합니다.</div>
    <button type="button" className="kb" disabled={busy} onClick={refresh}>{opened ? '장비 정보 새로고침' : '장비 정보 조회'}</button>
    {opened && busy && <Loading label="장비 정보 조회 중" />}
    {opened && !busy && failed && <ErrorState error={units.error ?? history.error} onRetry={refresh} />}
    {opened && !busy && !failed && units.isSuccess && history.isSuccess && <>
      <div className="kv-sub" style={{ margin: '10px 0 4px' }}>장비 후보와 연결 상태</div>
      <KvGrid label="장비 후보" columns={columns} rows={units.data.data.items} rowKey={({ unit }) => unit.id} fallback="조회 가능한 장비가 없습니다." />
      <div className="kv-sub" style={{ margin: '10px 0 4px' }}>확정 장비의 이전 문의</div>
      {history.data.data.status === 'NO_CONFIRMED_UNIT'
        ? <div className="kv-sub" style={{ margin: 0 }}>확정 장비가 없어 이전 문의를 표시하지 않습니다.</div>
        : <>
          <div className="kv-sub" style={{ margin: '0 0 4px', color: 'var(--erp-text)' }}>확정 장비: {history.data.data.unit?.serial_no ?? '장비 정보 없음'}</div>
          {history.data.data.items.length === 0
            ? <div className="kv-sub" style={{ margin: 0 }}>조회 가능한 이전 문의가 없습니다.</div>
            : <ul style={{ margin: 0, padding: '0 0 0 16px' }}>{history.data.data.items.map(item => <li key={item.id}>
              <Link to={`/cs/tickets/${encodeURIComponent(item.id)}`}>{item.subject ?? item.external_id ?? item.id}</Link> {item.serial_no}
              <small style={{ display: 'block', color: 'var(--erp-muted)' }}>{item.relation === 'REPLACED_UNIT' ? '교체 전 장비 이력 — 설치·출하 정보를 현재 장비에 복사하지 않습니다.' : '동일 장비 이력'}</small>
            </li>)}</ul>}
        </>}
      {canWrite && picked
        ? <UnitLinkForm key={`${picked.unit.id}:${picked.link?.version ?? 0}`} row={picked} busy={saving} notice={notice} onSave={form => void save(form)} />
        : <div className="kv-sub" style={{ margin: '8px 0 0' }}>장비 연결 확정·수정은 실제 API 개발 모드에서만 할 수 있습니다.</div>}
    </>}
  </div>;
}

function UnitLinkForm({ row, busy, notice, onSave }: { row: UnitRow; busy: boolean; notice: { kind: 'ok' | 'error'; text: string } | null; onSave: (form: FormData) => void }) {
  const [status, setStatus] = useState<Status>(row.link?.link_status as Status ?? 'CANDIDATE');
  const [repeat, setRepeat] = useState<string>(row.link?.repeat_type ?? '');
  // 고객·제품이 다른 장비는 "아님"만 기록할 수 있고, 확정에는 "다른 장비" 관계를 쓸 수 없다(서버가 422로 거부).
  const mismatched = !row.matches_ticket;
  const repeatInvalid = status === 'CONFIRMED' && repeat === 'DIFFERENT_UNIT';
  return <form aria-label="장비 연결" style={{ paddingTop: 10 }} onSubmit={event => { event.preventDefault(); if (!repeatInvalid) onSave(new FormData(event.currentTarget)); }}>
    <div className="kv-sub" style={{ margin: '0 0 6px', color: 'var(--erp-text)' }}>{row.unit.serial_no}</div>
    <div className="kv-field"><span className="req">이 장비인가</span>
      <select name="status" aria-label="이 장비인가" value={status} onChange={event => setStatus(event.target.value as Status)}>
        {STATUS_OPTIONS.map(([value, label]) => <option key={value} value={value} disabled={mismatched && value !== 'REJECTED'}>{label}</option>)}
      </select></div>
    <div className="kv-field"><span className="req">확인 방법</span>
      <select name="basis" aria-label="확인 방법" defaultValue={row.link?.link_basis ?? 'SERIAL_PROVIDED'}>{BASIS_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
    <div className="kv-field"><span>이전 문의와 관계</span>
      <select name="repeat" aria-label="이전 문의와 관계" value={repeat} onChange={event => setRepeat(event.target.value)}>
        <option value="">해당 없음</option>{REPEAT_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
    {mismatched && <div className="kv-sub" style={{ margin: '6px 0 0' }}>고객 또는 제품이 문의와 달라 "아님"만 기록할 수 있습니다.</div>}
    {repeatInvalid && <div className="kv-sub" role="alert" style={{ margin: '6px 0 0' }}>확정한 장비의 관계를 "다른 장비"로 둘 수 없습니다.</div>}
    {notice && <div className="kv-sub" role={notice.kind === 'error' ? 'alert' : 'status'} style={{ margin: '6px 0 0' }}>{notice.text}</div>}
    <div className="kv-actions"><button type="submit" className="kb primary" disabled={busy || repeatInvalid || (mismatched && status !== 'REJECTED')}>{busy ? '저장 중' : '저장'}</button></div>
  </form>;
}
