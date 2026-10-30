import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects, type ProjectSummary } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime, PROJECT_STATUS_LABEL } from '../components/tft';
import { KvBox, KvField, KvGrid, KvNext, KvSplit, KvTitle, type KvColumn } from '../ls-shell/kv';

const TERMS: [string, string][] = [
  ['요구', '규격서 조항에서 뽑은 항목 하나. 값과 근거 조항이 함께 붙습니다.'],
  ['근거 확인', '자사 자료로 요구를 충족한다고 확인된 상태입니다.'],
  ['조건부', '값은 맞을 수 있지만 조건이나 단위 확인이 남은 상태입니다.'],
  ['사양 차이', '같은 조건에서 비교해 요구에 못 미치는 상태입니다.'],
  ['근거 부족', '판단할 자료가 없는 상태. 자료에 없다고 미충족은 아닙니다.'],
  ['보완 경로', '사양 차이나 근거 부족을 메우는 방법(자사 대안, 협력사 등)입니다.'],
];

const COLUMNS: KvColumn<ProjectSummary>[] = [
  { key: 'name', header: '프로젝트', className: 't', cell: p => <Link to={`/tft/projects/${p.id}`}>{p.name}</Link> },
  { key: 'status', header: '상태', width: 96, className: 'c', cell: p => PROJECT_STATUS_LABEL[p.status] ?? p.status },
  { key: 'created', header: '접수일시', width: 168, className: 'd', cell: p => formatDateTime(p.created_at) },
];

// 업무 홈 TF0001. 프로젝트별 건수는 조회 API에 없어 받아 온 목록을 상태별로 세는 데까지만 보여 준다.
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
      <Link className="kb" to="/tft/projects">프로젝트 목록</Link>
    </KvTitle>
    <KvNext>프로젝트를 열어 요구와 근거를 확인하세요. 프로젝트별 할 일 건수는 조회 API가 연결되면 표시됩니다.</KvNext>
    <KvSplit columns="1fr 1.15fr">
      <KvBox title="업무 개요" note="받아 온 첫 쪽을 상태별로 센 것" bodyStyle={{ padding: 0 }}>
        <div className="kv-steps" style={{ margin: 0, border: 0, borderBottom: '1px solid var(--erp-border-soft)' }}>
          {Object.entries(PROJECT_STATUS_LABEL).map(([code, label]) =>
            <span key={code} style={{ height: 30, lineHeight: '30px' }}>{label} <b style={{ fontWeight: 400, fontSize: 'inherit' }}>{projects.isSuccess ? count(code) : '-'}</b></span>)}
        </div>
        <div style={{ padding: '8px 10px', fontSize: '12.5px', lineHeight: 1.55, color: 'var(--erp-text)' }}>
          규격서를 올리면 AI가 조항에서 요구를 뽑아 자사 자료와 견줍니다. 담당자는 근거를 확인하고, 모자란 요구는 보완 경로를 정한 뒤 초안을 만듭니다.
        </div>
      </KvBox>
      <KvBox title="용어 풀이" bodyStyle={{ padding: '6px 10px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 14px' }}>
        {TERMS.map(([term, text]) => <KvField key={term} label={term}>{text}</KvField>)}
      </KvBox>
    </KvSplit>
    <div className="kv-gridhead"><span>최근 프로젝트</span></div>
    {projects.isError
      ? <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
      : <KvGrid
        label="최근 프로젝트"
        columns={COLUMNS}
        rows={items}
        rowKey={p => p.id}
        fallback={projects.isPending ? <Loading /> : '프로젝트가 없습니다.'}
      />}
  </main>;
}
