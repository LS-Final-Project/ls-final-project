import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects, type ProjectFilters, type ProjectSummary } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvBox, KvField, KvGrid, KvKey, KvNext, KvSplit, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { ProjectPagination, ProjectSearchField } from './ProjectPagination';
import { JUDGEMENT_LABEL, label, OUTPUT_LABEL } from './labels';

const TERMS: [string, string][] = [
  ['진행', '프로젝트가 어디까지 왔는지. 접수, 분석 중, 검토본 작성 중 하나입니다.'],
  ['판단', '사람 확인이 어디까지 됐는지. 검토 필요, 조건 확인 필요, 담당자 확인 순입니다.'],
  ['미해결 필수', '꼭 맞춰야 하는데 아직 우리 제품으로 된다는 근거를 못 찾은 요구. 0이어야 제안서를 낼 수 있습니다.'],
  ['승인 대기', '누군가 판단을 바꾸자고 올렸는데 책임자가 아직 승인하지 않은 변경 제안.'],
  ['열린 질문', '고객에게 물어봤는데 아직 답을 받지 못한 질문.'],
  ['검토본', '요구 하나하나에 대응 여부와 근거를 적은 결과물. 제안서 작성의 바탕입니다.'],
];

// 보드의 다음 행동 열. 남은 일을 급한 순서로 적는다.
function nextAction(p: ProjectSummary) {
  if (p.requirements_count === 0) return '문서 올리기, 처리 실행';
  const todo = [
    p.pending_proposals_count > 0 && `승인 대기 ${p.pending_proposals_count}건 처리`,
    p.open_questions_count > 0 && `열린 질문 ${p.open_questions_count}건 회신 기록`,
    p.unresolved_mandatory_count > 0 && `미해결 필수 ${p.unresolved_mandatory_count}건 판단`,
  ].filter(Boolean);
  return todo.join(', ') || '없음';
}
const columns = (page: number): KvColumn<ProjectSummary>[] => [
  { key: 'no', header: 'No', width: 40, className: 'n', cell: (_p, index) => (page - 1) * 10 + index + 1 },
  { key: 'code', header: '프로젝트', width: 96, cell: p => p.external_id ? <Link to={`/tft/projects/${p.id}`}>{p.external_id}</Link> : '' },
  { key: 'name', header: '프로젝트명', className: 't', cell: p => <Link to={`/tft/projects/${p.id}`}>{p.name}</Link> },
  { key: 'output', header: '진행', width: 84, className: 'c', cell: p => label(OUTPUT_LABEL, p.output_status) },
  { key: 'judgement', header: '판단', width: 104, className: 'c', cell: p => label(JUDGEMENT_LABEL, p.judgement_status) },
  { key: 'unresolved', header: '미해결 필수', width: 76, className: 'c', cell: p => p.requirements_count ? p.unresolved_mandatory_count : '' },
  { key: 'pending', header: '승인 대기', width: 64, className: 'c', cell: p => p.requirements_count ? p.pending_proposals_count : '' },
  { key: 'questions', header: '열린 질문', width: 64, className: 'c', cell: p => p.requirements_count ? p.open_questions_count : '' },
  { key: 'next', header: '다음 행동', cell: nextAction },
];
// 업무 홈 TF0001(TF_K_Home). 업무 건수는 현재 페이지를 더한 값이다. 보드의 최근 작업·공지는 API가 없어 영역을 두지 않는다.
export default function RfpHome() {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const queryClient = useQueryClient();
  const reload = useCallback(() => { queryClient.invalidateQueries(); }, [queryClient]);
  useHotkey('F8', reload, access.ready);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<ProjectFilters>({});
  const [text, setText] = useState('');
  const [field, setField] = useState<'all' | 'external_id' | 'name'>('all');
  const search = () => { if (text.trim().length === 1) return; setFilters({ q: text.trim() || undefined, search_field: field }); setPage(1); };
  const projects = useQuery({ queryKey: ['projects', mode, access.user.data?.data.id, filters, page], queryFn: () => listProjects(filters, undefined, page), enabled: access.ready });
  if (!access.ready) return <BusinessGate access={access} title="업무 홈" />;
  const items = projects.data?.data.items ?? [];
  const count = (status: string) => items.filter(p => p.output_status === status).length;
  const sum = (pick: (p: ProjectSummary) => number) => items.reduce((total, p) => total + pick(p), 0);
  const todo = items.filter(p => nextAction(p) !== '없음').length;
  // 프로젝트가 가장 많은 단계를 강조한다.
  const busiest = Math.max(...Object.keys(OUTPUT_LABEL).map(count));
  return <main>
    <KvTitle path="제안 지원" title="업무 홈" code="TF0001">
      <button className="kb primary" onClick={reload}>조회<KvKey>F8</KvKey></button>
      <Link className="kb" to="/tft/projects">접수 및 요구 검토</Link>
      <button className="kb" onClick={() => window.print()}>인쇄</button>
    </KvTitle>
    <KvNext>{projects.isSuccess
      ? `현재 페이지 ${items.length}건, 할 일 있는 프로젝트 ${todo} | 미해결 필수 ${sum(p => p.unresolved_mandatory_count)}, 승인 대기 ${sum(p => p.pending_proposals_count)}, 열린 질문 ${sum(p => p.open_questions_count)}`
      : '내 프로젝트를 불러오는 중입니다.'}</KvNext>
    <KvSplit columns="1fr 1.15fr">
      <KvBox title="업무 개요" note={projects.isSuccess ? `내 프로젝트 ${items.length}건, 단계별, 현재 페이지 기준` : undefined} bodyStyle={{ padding: 0 }}>
        <div className="kv-steps" style={{ margin: 0, border: 0, borderBottom: '1px solid var(--erp-border-soft)' }}>
          {Object.entries(OUTPUT_LABEL).map(([code, text]) =>
            <span key={code} className={projects.isSuccess && busiest > 0 && count(code) === busiest ? 'now' : undefined} style={{ height: 30, lineHeight: '30px' }}>{text} <b style={{ fontWeight: 400, fontSize: 'inherit' }}>{projects.isSuccess ? count(code) : '-'}</b></span>)}
        </div>
        <div style={{ padding: '8px 10px', fontSize: '12.5px', lineHeight: 1.55, color: 'var(--erp-text)' }}>
          고객 제안요청서(사양서, 도면)를 올리면 AI가 요구사항을 뽑아 우리 제품 자료와 맞춰 봅니다. 영업 담당자는 못 맞춘 필수 요구를 판단하고, 변경 제안을 승인하고, 고객 질문의 답을 기록합니다. 모두 정리되면 검토본을 내보내 제안서에 씁니다.
        </div>
      </KvBox>
      <KvBox title="용어 풀이" bodyStyle={{ padding: '6px 10px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 14px' }}>
        {TERMS.map(([term, text]) => <KvField key={term} label={term}>{text}</KvField>)}
      </KvBox>
    </KvSplit>
    <form className="rfp-project-search" aria-label="프로젝트 검색" onSubmit={event => { event.preventDefault(); search(); }}>
      <ProjectSearchField value={field} onChange={setField} />
      <input aria-label="검색어" value={text} maxLength={100} placeholder="번호 또는 이름, 2자 이상" onChange={event => setText(event.target.value)} />
      <button className="kb primary" type="submit" disabled={projects.isFetching || text.trim().length === 1}>검색</button>
      {text.trim().length === 1 && <span>검색어는 두 글자 이상 입력하세요.</span>}
    </form>
    <div className="kv-gridhead"><span>내 프로젝트{projects.isSuccess ? ` ${items.length}건` : ''}</span></div>
    {projects.isError
      ? <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
      : <KvGrid
        label="내 프로젝트"
        columns={columns(page)}
        rows={items}
        rowKey={p => p.id}
        fallback={projects.isPending ? <Loading /> : '프로젝트가 없습니다.'}
      />}
    {!projects.isError && <ProjectPagination page={page} total={projects.data?.data.total} busy={projects.isFetching} onPage={setPage} />}
  </main>;
}
