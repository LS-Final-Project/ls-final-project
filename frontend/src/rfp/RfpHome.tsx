import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects, type ProjectSummary } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime, PROJECT_STATUS_LABEL } from '../components/tft';
import { KvBox, KvField, KvGrid, KvNext, KvSplit, KvTitle, type KvColumn } from '../ls-shell/kv';

const TERMS: [string, string][] = [
  ['진행', '프로젝트가 어디까지 왔는지. 접수, 분석 중, 검토본 작성 중 하나입니다.'],
  ['판단', '사람 확인이 어디까지 됐는지. 검토 필요, 조건 확인 필요, 담당자 확인 순입니다.'],
  ['미해결 필수', '꼭 맞춰야 하는데 아직 우리 제품으로 된다는 근거를 못 찾은 요구. 0이어야 제안서를 낼 수 있습니다.'],
  ['승인 대기', '누군가 판단을 바꾸자고 올렸는데 책임자가 아직 승인하지 않은 변경 제안.'],
  ['열린 질문', '고객에게 물어봤는데 아직 답을 받지 못한 질문.'],
  ['검토본', '요구 하나하나에 대응 여부와 근거를 적은 결과물. 제안서 작성의 바탕입니다.'],
];

const COLUMNS: KvColumn<ProjectSummary>[] = [
  { key: 'no', header: 'No', width: 40, className: 'n', cell: (_p, index) => index + 1 },
  { key: 'code', header: '프로젝트', width: 96, cell: p => p.external_id ?? '' },
  { key: 'name', header: '프로젝트명', className: 't', cell: p => <Link to={`/tft/projects/${p.id}`}>{p.name}</Link> },
  { key: 'status', header: '진행', width: 96, className: 'c', cell: p => PROJECT_STATUS_LABEL[p.status] ?? p.status },
];
const PENDING = '연결 전입니다. 처리 기록 API가 연결되면 표시됩니다.';
const blank = (key: string, header: string, width?: number): KvColumn<string> => ({ key, header, width, cell: value => value });
const EMPTY_LOG = [blank('at', '일시', 56), blank('project', '프로젝트', 84), blank('what', '내용'), blank('by', '처리자', 64)];
const EMPTY_NOTICE = [blank('at', '날짜', 56), blank('what', '내용')];

// 업무 홈 TF0001(TF_K_Home). 판단·건수·다음 행동 열은 목록 API에 칸이 오면 COLUMNS에 더한다.
export default function RfpHome() {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const queryClient = useQueryClient();
  const projects = useQuery({ queryKey: ['projects', mode, undefined], queryFn: () => listProjects(), enabled: access.ready });
  if (!access.ready) return <BusinessGate access={access} title="업무 홈" />;
  const items = projects.data?.data.items ?? [];
  const count = (status: string) => items.filter(p => p.status === status).length;
  return <main>
    <KvTitle path="제안 지원" title="업무 홈" code="TF0001">
      <button className="kb primary" onClick={() => queryClient.invalidateQueries()}>조회</button>
      <Link className="kb" to="/tft/projects">접수 및 요구 검토</Link>
      <button className="kb" onClick={() => window.print()}>인쇄</button>
    </KvTitle>
    <KvNext>{projects.isSuccess ? `내 프로젝트 ${items.length}건. 프로젝트를 열어 요구와 근거를 확인하세요.` : '내 프로젝트를 불러오는 중입니다.'}</KvNext>
    <KvSplit columns="1fr 1.15fr">
      <KvBox title="업무 개요" note={projects.isSuccess ? `내 프로젝트 ${items.length}건, 첫 쪽 기준` : undefined} bodyStyle={{ padding: 0 }}>
        <div className="kv-steps" style={{ margin: 0, border: 0, borderBottom: '1px solid var(--erp-border-soft)' }}>
          {Object.entries(PROJECT_STATUS_LABEL).map(([code, label]) =>
            <span key={code} style={{ height: 30, lineHeight: '30px' }}>{label} <b style={{ fontWeight: 400, fontSize: 'inherit' }}>{projects.isSuccess ? count(code) : '-'}</b></span>)}
        </div>
        <div style={{ padding: '8px 10px', fontSize: '12.5px', lineHeight: 1.55, color: 'var(--erp-text)' }}>
          고객 제안요청서(사양서, 도면)를 올리면 AI가 요구사항을 뽑아 우리 제품 자료와 맞춰 봅니다. 영업 담당자는 못 맞춘 필수 요구를 판단하고, 변경 제안을 승인하고, 고객 질문의 답을 기록합니다. 모두 정리되면 검토본을 내보내 제안서에 씁니다.
        </div>
      </KvBox>
      <KvBox title="용어 풀이" bodyStyle={{ padding: '6px 10px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 14px' }}>
        {TERMS.map(([term, text]) => <KvField key={term} label={term}>{text}</KvField>)}
      </KvBox>
    </KvSplit>
    <div className="kv-gridhead"><span>내 프로젝트{projects.isSuccess ? ` ${items.length}건` : ''}</span></div>
    {projects.isError
      ? <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
      : <KvGrid
        label="내 프로젝트"
        columns={COLUMNS}
        rows={items}
        rowKey={p => p.id}
        fallback={projects.isPending ? <Loading /> : '프로젝트가 없습니다.'}
      />}
    <KvSplit>
      <div>
        <div className="kv-sub" style={{ margin: '12px 0 6px' }}>최근 작업, 오늘</div>
        <KvGrid label="최근 작업" columns={EMPTY_LOG} rows={[]} rowKey={String} fallback={PENDING} />
      </div>
      <div>
        <div className="kv-sub" style={{ margin: '12px 0 6px' }}>공지</div>
        <KvGrid label="공지" columns={EMPTY_NOTICE} rows={[]} rowKey={String} fallback="공지 API가 아직 없습니다." />
      </div>
    </KvSplit>
  </main>;
}
