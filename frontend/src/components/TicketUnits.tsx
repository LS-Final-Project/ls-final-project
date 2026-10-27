import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getTicketUnits, getUnitHistory } from '../api/cs';
import { ErrorState, Loading, Preparing } from './states';

// KAN-102 조회 계약만 소비한다. 운영 중계 allowlist 확장이나 장비 확정 쓰기는 이 변경에 포함하지 않는다.
export default function TicketUnits({ ticketId }: { ticketId: string }) {
  const [opened, setOpened] = useState(false);
  const mode = getApiMode();
  const enabled = import.meta.env.DEV && opened;
  const units = useQuery({ queryKey: ['ticket-units', mode, ticketId], queryFn: () => getTicketUnits(ticketId), enabled });
  const history = useQuery({ queryKey: ['unit-history', mode, ticketId], queryFn: () => getUnitHistory(ticketId), enabled });
  if (!import.meta.env.DEV) return <Preparing inline title="장비와 이전 문의" message="장비 조회의 운영 연결을 준비 중입니다." />;
  const failed = units.isError || history.isError;
  const busy = units.isFetching || history.isFetching;
  const refresh = () => { setOpened(true); if (opened) { void units.refetch(); void history.refetch(); } };
  return <section><h2>장비와 이전 문의</h2>
    <p>후보 장비는 확정 장비가 아닙니다. 이전 문의는 확정된 장비 또는 교체 전 장비의 이력만 표시합니다.</p>
    <button disabled={busy} onClick={refresh}>{opened ? '장비 정보 새로고침' : '장비 정보 조회'}</button>
    {opened && busy && <Loading label="장비 정보 조회 중" />}
    {opened && failed && <ErrorState error={units.error ?? history.error} onRetry={refresh} />}
    {opened && !failed && units.isSuccess && history.isSuccess && <>
      <h3>장비 후보와 연결 상태</h3>
      {units.data.data.items.length === 0 ? <p>조회 가능한 장비가 없습니다.</p> : <ul>{units.data.data.items.map(({ unit, link, matches_ticket }) => <li key={unit.id}>
        <strong>{unit.serial_no}</strong> · {unit.product_code} · 고객 {unit.customer_external_id}
        <p>{!matches_ticket ? '문의의 고객·제품과 불일치' : '문의의 고객·제품과 일치'} · {link?.link_status === 'CONFIRMED' ? '확정' : link?.link_status === 'REJECTED' ? '제외' : '후보'}</p>
        <dl><dt>설치일</dt><dd>{unit.commissioning_date ?? '미정'} ({unit.commissioning_status})</dd><dt>출하일</dt><dd>{unit.ship_date ?? '미정'}</dd><dt>최근 FW</dt><dd>{unit.fw_version_last ?? '미정'}</dd><dt>출처</dt><dd>{unit.data_origin} · {unit.derivation_kind}</dd></dl>
      </li>)}</ul>}
      <h3>확정 장비의 이전 문의</h3>
      {history.data.data.status === 'NO_CONFIRMED_UNIT' ? <p>확정 장비가 없어 이전 문의를 표시하지 않습니다.</p> : <>
        <p>확정 장비: {history.data.data.unit?.serial_no ?? '장비 정보 없음'}</p>
        {history.data.data.items.length === 0 ? <p>조회 가능한 이전 문의가 없습니다.</p> : <ul>{history.data.data.items.map(item => <li key={item.id}>
          <Link to={`/cs/tickets/${encodeURIComponent(item.id)}`}>{item.subject ?? item.external_id ?? item.id}</Link> · {item.serial_no} · {item.relation === 'REPLACED_UNIT' ? '교체 전 장비 이력 — 설치·출하 정보를 현재 장비에 복사하지 않습니다.' : '동일 장비 이력'}
        </li>)}</ul>}
      </>}
      <p>장비 연결 확정·수정은 이 조회 화면에서 수행하지 않습니다.</p>
    </>}
  </section>;
}
