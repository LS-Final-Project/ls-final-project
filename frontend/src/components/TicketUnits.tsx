import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getTicketUnits, getUnitHistory, type TicketUnitsResponse } from '../api/cs';
import { KvGrid, type KvColumn } from '../ls-shell/kv';
import { ErrorState, Loading } from './states';

type UnitRow = TicketUnitsResponse['data']['items'][number];
const LINK_LABEL: Record<string, string> = { CONFIRMED: '확정', REJECTED: '제외' };

// 설치 장비 확인 패널의 본문. 조회 계약(KAN-102)만 소비하고, 장비 연결 확정·수정(쓰기)은 이 화면에서 하지 않는다.
// 운영 중계 allowlist 확장도 포함하지 않는다. 패널 틀(제목 줄)은 문의 처리 화면이 그린다.
export default function TicketUnits({ ticketId }: { ticketId: string }) {
  const [opened, setOpened] = useState(false);
  const mode = getApiMode();
  const enabled = import.meta.env.DEV && opened;
  const units = useQuery({ queryKey: ['ticket-units', mode, ticketId], queryFn: ({ signal }) => getTicketUnits(ticketId, signal), enabled, staleTime: 0 });
  const history = useQuery({ queryKey: ['unit-history', mode, ticketId], queryFn: ({ signal }) => getUnitHistory(ticketId, signal), enabled, staleTime: 0 });
  if (!import.meta.env.DEV) return <div className="kv-sub" style={{ margin: 0 }}>장비 조회의 운영 연결을 준비 중입니다.</div>;
  const failed = units.isError || history.isError;
  const busy = units.isFetching || history.isFetching;
  const refresh = () => { setOpened(true); if (opened) { void units.refetch(); void history.refetch(); } };

  const columns: KvColumn<UnitRow>[] = [
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
      <div className="kv-sub" style={{ margin: '8px 0 0' }}>장비 연결 확정·수정은 이 조회 화면에서 수행하지 않습니다.</div>
    </>}
  </div>;
}
