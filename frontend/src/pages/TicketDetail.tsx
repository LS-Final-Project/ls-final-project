import { type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import TicketWorkflow from '../components/TicketWorkflow';
import TicketUnits from '../components/TicketUnits';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { getApiMode } from '../api/client';
import { getTicket, getTicketAnalysis } from '../api/cs';
import { ErrorState, Loading } from '../components/states';
import { KvKey, KvMore, KvTitle, useHotkey } from '../ls-shell/kv';
import { CHANNEL_LABEL, STATE_LABEL, formatStamp } from '../ls-shell/cs-labels';

const PENDING = '연결 전입니다.';
const Cell = ({ label, children }: { label: string; children: ReactNode }) =>
  <label><span>{label}</span><div className="v">{children}</div></label>;
const Row = ({ children }: { children: ReactNode }) =>
  <div className="row" style={{ gridTemplateColumns: 'repeat(4,1fr)' }}>{children}</div>;

// 문의 처리 CS0102. 지금 API는 접수 원문·업무 상태·장비 후보 조회까지다.
// 저장, 부서 검토 요청, 종결 같은 쓰기 동작과 AI 분류·요약, 처리 기록은 연결 전이라 비활성이나 준비 중으로 둔다.
export default function TicketDetail() {
  const { ticketId = '' } = useParams();
  const navigate = useNavigate();
  const mode = getApiMode();
  const access = useBusinessAccess('cs');
  const ticket = useQuery({ queryKey: ['tickets', mode, ticketId], queryFn: ({ signal }) => getTicket(ticketId, signal), enabled: access.ready && ticketId !== '' });
  // 기한을 읽는 사람이 등급이 추정임을 알아야 한다. 안내일 뿐이라 분석을 못 읽어도 화면은 그대로다.
  const analysis = useQuery({ queryKey: ['ticket-analysis', mode, ticketId], queryFn: ({ signal }) => getTicketAnalysis(ticketId, signal), enabled: access.ready && ticketId !== '', retry: false });
  const tierNote = analysis.data?.data?.rules?.tier_note;
  useHotkey('Escape', () => navigate('/cs/tickets'), access.ready);
  if (!access.ready) return <BusinessGate access={access} title="문의 처리" />;
  const data = ticket.isSuccess ? ticket.data.data : undefined;
  const rc = data?.resolution_case;
  const state = data ? STATE_LABEL[data.workflow_state] ?? '알 수 없는 상태, 확인 필요' : '-';
  return <main>
    <KvTitle path="C/S 관리" title="문의 처리" code="CS0102">
      <button className="kb primary" disabled title="저장할 항목이 아직 없습니다">저장<KvKey>F10</KvKey></button>
      <button className="kb" disabled title="부서 검토 요청 연결 전입니다">부서 검토 요청</button>
      <Link className="kb" to="/cs/tickets">목록<KvKey>Esc</KvKey></Link>
      <KvMore items={[
        { label: '검토 시작', note: data && data.workflow_state !== 'RECEIVED' ? '이미 검토중' : '연결 전' },
        { label: '요청 취소', note: '연결 전' },
        { label: '담당 변경', note: 'C/S 관리자만, 연결 전' },
        { label: '종결', note: '확인 완료된 검토 답변 또는 고객 발송 기록 필요' },
        { label: '해결 기록 남기기', note: '종결 후 한 건' },
      ]} />
    </KvTitle>

    <div className="kv-next">
      <span className="k">다음 할 일</span>
      {data ? <TicketWorkflow state={data.workflow_state} /> : <span>{ticket.isPending ? '불러오는 중' : '문의를 불러오지 못했습니다.'}</span>}
      <button className="kb" style={{ marginLeft: 'auto', flex: 'none', whiteSpace: 'nowrap' }} disabled={ticket.isFetching} onClick={() => void ticket.refetch()}>최신 상태 조회</button>
    </div>
    <div className="kv-sub" style={{ margin: '4px 14px 0' }}>API 조회 상태와 상태별 일반 안내입니다. 실행 권한이나 후속 작업의 완료를 보장하지 않습니다. 요약, 분류, 문안은 아직 없음, KAN-103과 106 뒤</div>

    {tierNote && <div className="kv-sub" role="note" style={{ margin: '4px 14px 0' }}>{tierNote}</div>}

    {ticket.isPending && <Loading />}
    {ticket.isError && <ErrorState error={ticket.error} onRetry={() => void ticket.refetch()} />}
    {data && <>
      <div className="kv-form">
        <Row>
          <Cell label="문의번호"><span className="ro">{data.external_id ?? data.id.slice(0, 8)}</span></Cell>
          <Cell label="접수일시"><span className="ro">{formatStamp(data.received_at)}</span></Cell>
          <Cell label="고객"><span className="ro">{data.customer_external_id}</span></Cell>
          <Cell label="접수 경로"><span className="ro">{CHANNEL_LABEL[data.channel]}, {data.body_language}</span></Cell>
        </Row>
        <Row>
          <Cell label="제품"><span className="ro">{data.product_code}</span></Cell>
          <Cell label="상태"><span className="ro">{state}</span></Cell>
          <Cell label="담당"><span className="ro unc" title="담당자 조회는 연결 전입니다">-</span></Cell>
          <Cell label="자료 구분"><span className="ro">{data.data_origin}, {data.derivation_kind}</span></Cell>
        </Row>
      </div>

      <div className="kv-split c3">
        <section className="kv-box">
          <h2>고객 원문<small>수신 {formatStamp(data.received_at)}</small></h2>
          <div className="body">
            <div className="kv-sub" style={{ margin: '0 0 4px', display: 'flex', alignItems: 'center' }}>한국어 요약<span className="unc" style={{ marginLeft: 8 }}>아직 없음, KAN-103 뒤</span></div>
            <div className="kv-sub" style={{ margin: '10px 0 4px', color: 'var(--erp-text)' }}>{data.subject || '(제목 없음)'}</div>
            <div className="kv-text en scroll">{data.body}</div>
            <div className="kv-sub" style={{ margin: '8px 0 4px' }}>보고된 오류 코드 {data.error_code_reported ?? '-'}, 보고된 FW {data.fw_version_reported ?? '-'}</div>
          </div>
        </section>
        <section className="kv-box">
          <h2>설치 장비 확인<small>확인 전</small></h2>
          <div className="body"><div className="ls-legacy" style={{ minHeight: 0, padding: 0 }}><TicketUnits key={ticketId} ticketId={ticketId} /></div></div>
        </section>
        <section className="kv-box ai-pre">
          <h2>AI 분류<span className="unc" style={{ marginLeft: 8 }}>아직 없음, KAN-103 뒤</span></h2>
          <div className="body">
            <div className="kv-field"><span>주분류</span><select aria-label="주분류" disabled><option>-</option></select></div>
            <div className="kv-field"><span>보조분류</span><select aria-label="보조분류" disabled><option>-</option></select></div>
            <div className="kv-field"><span>긴급 여부</span><select aria-label="긴급 여부" disabled><option>-</option></select></div>
            <div className="kv-field"><span>반품 유형</span><select aria-label="반품 유형" disabled><option>-</option></select></div>
            <div className="kv-sub" style={{ margin: '8px 0 0' }}>AI 분류·담당자 수정 결과는 아직 연결되지 않았습니다. 원문 정보와 구분해 확인하세요.</div>
          </div>
        </section>
      </div>

      <div className="kv-split">
        <div>
          <div className="kv-sub" style={{ margin: '0 0 6px' }}>검토 요청</div>
          <div className="kv-sub" style={{ margin: 0, padding: '6px 10px', border: '1px solid var(--erp-border)', background: '#fff', color: 'var(--erp-muted)' }}>요청 이력 조회는 {PENDING}</div>
          <div className="kv-sub" style={{ margin: '12px 0 0' }}>알려진 해결 사례</div>
          <div className="kv-sub" style={{ margin: 0, padding: '6px 10px', border: '1px solid var(--erp-border)', background: '#fff', color: 'var(--erp-text)' }}>
            {rc
              ? <>사례 {rc.external_id ?? '-'}. 증상 {rc.symptom}. 원인 {rc.root_cause} ({rc.root_cause_type}). 조치 {rc.action_taken}. 처리 부서 {rc.resolved_by_dept}. 현장 방문 {rc.onsite_visit ? '있음' : '없음'}. 재발 방지 {rc.recurrence_prevention ?? '-'}</>
              : '연결된 해결 사례가 없습니다.'}
          </div>
        </div>
        <div>
          <div className="kv-sub" style={{ margin: '0 0 6px' }}>처리 기록</div>
          <div className="kv-tablewrap"><table className="kv-grid">
            <colgroup><col style={{ width: 130 }} /><col style={{ width: 44 }} /><col style={{ width: 110 }} /><col /><col style={{ width: 44 }} /></colgroup>
            <thead><tr><th>일시</th><th className="c">구분</th><th>처리자</th><th>바뀐 내용</th><th className="c">순번</th></tr></thead>
            <tbody><tr><td colSpan={5}>처리 기록 조회는 {PENDING}</td></tr></tbody>
          </table></div>
        </div>
      </div>
    </>}
  </main>;
}
