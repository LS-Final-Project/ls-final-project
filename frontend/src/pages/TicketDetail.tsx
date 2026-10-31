import TicketWorkflow from '../components/TicketWorkflow';
import TicketUnits from '../components/TicketUnits';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getTicket } from '../api/cs';
import { ErrorState, Loading, Preparing } from '../components/states';

export default function TicketDetail() {
  const { ticketId = '' } = useParams();
  const mode = getApiMode();
  const access = useBusinessAccess('cs');
  const ticket = useQuery({queryKey: ['tickets', mode, ticketId], queryFn: ({signal}) => getTicket(ticketId, signal), enabled: access.ready && ticketId !== ''});
  if (!access.ready) return <BusinessGate access={access} title="C/S 문의" />;
  const data = ticket.isSuccess ? ticket.data.data : undefined;
  const rc = data?.resolution_case;
  return <main>
    <header><p className="eyebrow"><Link to="/cs/tickets">C/S 문의 목록</Link></p><h1>{data ? data.subject || '(제목 없음)' : '문의 상세'}</h1></header>
    <button disabled={ticket.isFetching} onClick={()=>ticket.refetch()}>최신 상태 조회</button>
    {ticket.isPending && <Loading />}
    {ticket.isError && <ErrorState error={ticket.error} onRetry={() => ticket.refetch()} />}
    {data && <>
      <section><h2>현재 업무 상태</h2><TicketWorkflow state={data.workflow_state}/><p>API 조회 상태와 상태별 일반 안내입니다. 실행 권한이나 후속 작업의 완료를 보장하지 않습니다.</p></section>
      <section><h2>접수 정보</h2>
        <dl><dt>외부 ID</dt><dd>{data.external_id ?? '-'}</dd><dt>채널</dt><dd>{data.channel}</dd><dt>제품</dt><dd>{data.product_code}</dd><dt>고객</dt><dd>{data.customer_external_id}</dd><dt>접수</dt><dd>{new Date(data.received_at).toLocaleString('ko-KR')}</dd><dt>언어</dt><dd>{data.body_language}</dd><dt>보고된 오류 코드</dt><dd>{data.error_code_reported ?? '-'}</dd><dt>보고된 FW</dt><dd>{data.fw_version_reported ?? '-'}</dd><dt>출처</dt><dd><small>{data.data_origin}</small> <small>{data.derivation_kind}</small></dd></dl>
        <h3>본문</h3><p className="body">{data.body}</p>
      </section>
      <TicketUnits key={ticketId} ticketId={ticketId} />
      <Preparing inline title="AI 분석과 담당자 검토" message="AI 분류·담당자 수정 결과는 아직 연결되지 않았습니다. 원문 정보와 구분해 확인하세요." />
      <section><h2>알려진 해결 사례</h2>
        {rc ? <dl><dt>사례</dt><dd>{rc.external_id ?? '-'}</dd><dt>증상</dt><dd>{rc.symptom}</dd><dt>원인</dt><dd>{rc.root_cause} <small>{rc.root_cause_type}</small></dd><dt>조치</dt><dd>{rc.action_taken}</dd><dt>처리 부서</dt><dd>{rc.resolved_by_dept}</dd><dt>현장 방문</dt><dd>{rc.onsite_visit ? '있음' : '없음'}</dd><dt>재발 방지</dt><dd>{rc.recurrence_prevention ?? '-'}</dd></dl> : <p>연결된 해결 사례가 없습니다.</p>}
      </section>
    </>}
  </main>;
}
