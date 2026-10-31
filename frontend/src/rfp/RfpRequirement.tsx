import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import {
  confirmQuestion, getRequirement, listClauses, listQuestions, listRequirements,
  type Clause, type Question, type RequirementDetail, type RequirementFilters,
} from '../api/rfp';
import { getProject } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime } from '../components/tft';
import { KvBox, KvField, KvGrid, KvNext, KvSplit, KvTitle, type KvColumn } from '../ls-shell/kv';
import { DEPARTMENT_LABEL, ISSUE_LABEL, label, NECESSITY_LABEL, OPERATOR_LABEL, OWNER_LABEL, PATH_LABEL, QUESTION_STATUS_LABEL, RND_CHECK_LABEL, STATUS_LABEL } from './labels';
import { Failure } from './proposal-shared';
import ProposalForm from './ProposalForm';
import { QuestionForm, ReplyForm } from './QuestionForms';

// 미해결 필수는 목록 응답에 미확인 건수가 없어 서버 건수와 같게 거를 수 없다. 서버 필터가 있는 둘만 둔다.
const WALKS: Record<string, { name: string; filters: RequirementFilters }> = {
  all: { name: '전체', filters: {} },
  rereview: { name: '재검토 필요만', filters: { needs_rereview: true } },
  open: { name: '열린 질문만', filters: { has_open_question: true } },
};

// 순회 띠. 대응표 조회 결과를 끝까지 읽어 앞뒤 요구를 찾는다.
function Walk({ projectId, id }: { projectId: string; id: string }) {
  const mode = getApiMode();
  const [walk, setWalk] = useState('all');
  const list = useInfiniteQuery({
    queryKey: ['rfp-requirements', mode, projectId, 'walk', walk],
    queryFn: ({ pageParam }) => listRequirements(projectId, WALKS[walk].filters, 100, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: last => last.data.next_cursor ?? undefined,
  });
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = list;
  useEffect(() => { if (hasNextPage && !isFetchingNextPage) fetchNextPage(); }, [hasNextPage, isFetchingNextPage, fetchNextPage]);
  const rows = list.data?.pages.flatMap(page => page.data.items) ?? [];
  const at = rows.findIndex(row => row.requirement_id === id);
  const before = at > 0 ? rows[at - 1] : undefined;
  // 지금 요구가 이 조건에 없으면(at = -1) 조건의 첫 요구로 간다.
  const after = rows[at + 1];
  const go = (row: typeof rows[number] | undefined, text: string) => row
    ? <Link className="kb" to={`/tft/projects/${projectId}/requirements/${row.requirement_id}`}>{text} {row.code}</Link>
    : <button className="kb" disabled>{text}</button>;
  return <div role="group" aria-label="순회" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', margin: '8px 14px 0' }}>
    {Object.entries(WALKS).map(([code, { name }]) => <button key={code} className={`kb${walk === code ? ' primary' : ''}`} aria-pressed={walk === code} onClick={() => setWalk(code)}>{name}</button>)}
    <span>{list.isError ? '목록을 읽지 못했습니다' : list.isPending ? '읽는 중' : at >= 0 ? `${rows.length}건 중 ${at + 1}번째` : `조건에 맞는 요구 ${rows.length}건, 이 요구는 없음`}</span>
    {go(before, '이전 요구')}
    {go(after, '다음 요구')}
  </div>;
}

// 삭제 값은 이전 판본 값이라 현행 문구와 나눠 보여 준다.
function Deleted({ values }: { values: Clause['deleted_values'] }) {
  if (values.length === 0) return null;
  return <p className="kv-sub" style={{ margin: '6px 0 0' }}>삭제 값 {values.length}건: <del>{values.map(value => value.text).join(', ')}</del> (이전 판본 값, 현행 문구에 섞지 않음)</p>;
}

function Source({ data }: { data: RequirementDetail }) {
  const mode = getApiMode();
  const { origin } = data;
  const [picked, setPicked] = useState<Clause>();
  const clauses = useInfiniteQuery({
    queryKey: ['rfp-clauses', mode, origin.document_version_id],
    queryFn: ({ pageParam }) => listClauses(origin.document_version_id, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: last => last.data.next_cursor ?? undefined,
  });
  const rows = clauses.data?.pages.flatMap(page => page.data.items) ?? [];
  return <KvSplit columns="1fr 2fr">
    <KvBox title="원문 조항" note={`${rows.length}건${clauses.hasNextPage ? ' 이상' : ''}`} bodyStyle={{ maxHeight: 280, overflow: 'auto' }}>
      {clauses.isError
        ? <ErrorState error={clauses.error} onRetry={() => clauses.refetch()} />
        : clauses.isPending ? <Loading /> : rows.length === 0 ? '조항이 없습니다.' : <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {rows.map(clause => <li key={clause.clause_id} style={{ paddingLeft: clause.depth * 10 }}>
            <button
              aria-current={clause.clause_id === (picked?.clause_id ?? origin.clause_id) ? 'true' : undefined}
              style={{ border: 0, background: 'none', padding: '2px 0', cursor: 'pointer', textAlign: 'left', font: 'inherit', fontWeight: clause.clause_id === origin.clause_id ? 500 : 300 }}
              onClick={() => setPicked(clause)}
            >{clause.clause_no} {clause.title ?? ''}</button>
          </li>)}
        </ul>}
      {clauses.hasNextPage && <button className="kb" disabled={clauses.isFetchingNextPage} onClick={() => clauses.fetchNextPage()}>조항 더 보기</button>}
    </KvBox>
    {picked
      ? <KvBox title={`${picked.clause_no}${picked.page != null ? `, p.${picked.page}` : ''}`} note="현행 문구">
        <div className="kv-text" lang="en">{picked.text}</div>
        <Deleted values={picked.deleted_values} />
        {picked.clause_id !== origin.clause_id && <button className="kb" style={{ marginTop: 6 }} onClick={() => setPicked(undefined)}>요구 원문으로</button>}
      </KvBox>
      : <KvBox title={`${origin.clause_no ?? '조항 없음'}${origin.page != null ? `, p.${origin.page}` : ''}`} note="요구가 인용한 문구">
        <div className="kv-text" lang="en">{origin.quote}</div>
        <Deleted values={origin.deleted_values} />
      </KvBox>}
  </KvSplit>;
}

type Panel = { kind: 'propose' } | { kind: 'question' } | { kind: 'reply'; id: string };

// 요구 상세와 조항 대조(TF0101). 상태·성격은 변경 제안으로만 바꾼다.
export default function RfpRequirement() {
  const { projectId = '', requirementId = '' } = useParams();
  // 순회로 요구를 옮기면 열린 폼과 알림을 새로 시작한다.
  return <Requirement key={requirementId} projectId={projectId} id={requirementId} />;
}

function Requirement({ projectId, id }: { projectId: string; id: string }) {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const client = useQueryClient();
  const [panel, setPanel] = useState<Panel>();
  const [notice, setNotice] = useState<string>();
  const requirement = useQuery({ queryKey: ['rfp-requirement', mode, id], queryFn: () => getRequirement(id), enabled: access.ready });
  const questions = useQuery({ queryKey: ['rfp-questions', mode, projectId], queryFn: () => listQuestions(projectId), enabled: access.ready });
  const project = useQuery({ queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled: access.ready });
  const reload = () => {
    for (const key of ['rfp-questions', 'rfp-requirement', 'rfp-requirements', 'rfp-proposals', 'projects']) client.invalidateQueries({ queryKey: [key, mode] });
  };
  const confirm = useMutation({
    // ponytail: 시도마다 새 키다. 확인은 DRAFT에서만 되므로 재전송은 409로 끝난다.
    mutationFn: (question: Question) => confirmQuestion(projectId, question.question_id, crypto.randomUUID()),
    onSuccess: reload,
  });
  if (!access.ready) return <BusinessGate access={access} title="접수 및 요구 검토" />;
  if (requirement.isPending) return <main><Loading /></main>;
  if (requirement.isError) return <main><ErrorState error={requirement.error} onRetry={() => requirement.refetch()} /></main>;
  const data = requirement.data.data;
  const { spec, judgement, links } = data;
  const all = questions.data?.data.items ?? [];
  // 이 요구의 질문을 앞에 둔다.
  const rows = [...all].sort((a, b) => Number(b.requirement_id === id) - Number(a.requirement_id === id));
  const waiting = all.filter(q => q.status === 'OPEN').length;
  // 다시 읽었더니 이미 회신된 질문이면 폼을 닫는다.
  const replying = panel?.kind === 'reply' ? all.find(q => q.question_id === panel.id && q.status === 'OPEN') : undefined;
  const value = [label(OPERATOR_LABEL, spec.operator), spec.value, spec.unit].filter(Boolean).join(' ');
  const pending = links.pending_proposal_ids.length;
  const nextStep = [
    pending > 0 && `변경 제안 ${pending}건 승인`,
    links.question_ids.length > 0 && `질문 ${links.question_ids.length}건 확인`,
  ].filter(Boolean).join(', ') || '변경 제안 또는 질문 작성';
  const columns: KvColumn<Question>[] = [
    { key: 'requirement', header: '요구', width: 72, className: 'c', cell: q => q.requirement_id === id ? '이 요구' : q.requirement_id ? <Link to={`/tft/projects/${projectId}/requirements/${q.requirement_id}`}>보기</Link> : '없음' },
    { key: 'issue', header: '유형', width: 96, className: 'c', cell: q => label(ISSUE_LABEL, q.issue_type) },
    { key: 'target', header: '대상', width: 84, className: 'c', cell: q => q.target === 'INTERNAL' ? label(DEPARTMENT_LABEL, q.target_dept) : '고객' },
    { key: 'text', header: '질문 내용', className: 't', cell: q => q.question_text },
    { key: 'status', header: '상태', width: 84, className: 'c', cell: q => label(QUESTION_STATUS_LABEL, q.status) },
    { key: 'reply', header: '회신', width: 200, cell: q => q.reply_received_at ? formatDateTime(q.reply_received_at) : q.internal_assumption ? `가정 ${q.internal_assumption.value}` : '' },
    { key: 'action', header: '처리', width: 112, className: 'c', cell: q => {
      if (q.status === 'DRAFT') return <button className="kb sm" disabled={confirm.isPending} onClick={() => confirm.mutate(q)}>보낸 질문으로</button>;
      if (q.status === 'OPEN') return <button className="kb sm" onClick={() => setPanel({ kind: 'reply', id: q.question_id })}>회신 기록</button>;
      return '';
    } },
  ];
  const connections = [
    ['자사 비교', links.comparison_summary ?? '없음', <Link key="t" to={`/tft/projects/${projectId}/table`}>대응표</Link>],
    ['보완 경로', label(PATH_LABEL, links.supplement_summary) || '없음', <Link key="s" to={`/tft/projects/${projectId}/supplement`}>보완</Link>],
    ['협력사 후보', `${links.partner_candidate_ids.length}건`, null],
    ['질문', `${links.question_ids.length}건`, null],
    ['승인 대기', `${pending}건`, pending > 0 ? <Link key="a" to={`/tft/projects/${projectId}/table/approvals`}>승인 화면</Link> : null],
    ['초안 문장', `${links.draft_sentence_ids.length}건`, <Link key="d" to={`/tft/projects/${projectId}/draft`}>초안</Link>],
  ] as const;
  return <main>
    <KvTitle path="제안 지원" title="접수 및 요구 검토" code="TF0101">
      <button className="kb" onClick={() => setPanel({ kind: 'propose' })}>변경 제안</button>
      <button className="kb" onClick={() => setPanel({ kind: 'question' })}>질문 작성</button>
      <Link className="kb" to={`/tft/projects/${projectId}/table`}>자사 대응표</Link>
      <Link className="kb" to={`/tft/projects/${projectId}`}>프로젝트</Link>
    </KvTitle>
    <KvNext>{[data.code, spec.attribute, label(NECESSITY_LABEL, spec.necessity), label(STATUS_LABEL, judgement.status), data.needs_rereview && '재검토 필요'].filter(Boolean).join(', ')}. 다음: {nextStep}</KvNext>
    <Walk projectId={projectId} id={id} />
    {notice && <p role="status" style={{ margin: '6px 14px 0' }}>{notice}</p>}
    {panel?.kind === 'propose' && <ProposalForm
      projectId={projectId}
      requirement={data}
      onCancel={() => setPanel(undefined)}
      onSaved={() => { setPanel(undefined); reload(); }}
      onReload={() => requirement.refetch()}
    />}
    {panel?.kind === 'question' && <QuestionForm projectId={projectId} requirement={data} departments={project.data?.data.review_departments ?? []} onCancel={() => setPanel(undefined)} onSaved={() => { setPanel(undefined); reload(); }} />}
    {replying && <ReplyForm
      key={replying.question_id}
      question={replying}
      onCancel={() => setPanel(undefined)}
      onReload={() => questions.refetch()}
      onSaved={proposed => {
        setPanel(undefined);
        setNotice(replying.target === 'INTERNAL' ? '회신을 기록했습니다.' : proposed ? '회신을 기록했습니다. 변경 제안이 생겼고 승인 전에는 요구가 바뀌지 않습니다.' : '회신을 기록했습니다. 줄어든 미확인 항목이 없어 변경 제안은 생기지 않았습니다.');
        reload();
      }}
    />}
    <Source key={data.origin.document_version_id} data={data} />
    <KvSplit columns="1fr 1fr">
      <KvBox title="요구" note={judgement.provenance_kind === 'ai_draft' ? 'AI 작성' : '직접 입력'}>
        <KvField label="장비, 작업">{[spec.equipment, spec.task].filter(Boolean).join(', ') || '없음'}</KvField>
        <KvField label="속성">{spec.attribute ?? '없음'}</KvField>
        <KvField label="요구값">{value || '없음'}</KvField>
        <KvField label="조건">{spec.conditions.map(c => [c.name, c.value, c.unit].filter(Boolean).join(' ')).join(', ') || '없음'}</KvField>
        <KvField label="규격">{spec.standard_system === 'NONE' ? '없음' : spec.standard_system}</KvField>
        <KvField label="성격">{label(NECESSITY_LABEL, spec.necessity)}</KvField>
        <KvField label="지정 모델">{spec.designated_model ?? '없음'}</KvField>
        <KvField label="미확정 항목">{spec.undetermined_items.join(', ') || '없음'}</KvField>
      </KvBox>
      <KvBox title="판단" note={`판본 ${data.version}`}>
        <KvField label="상태">{label(STATUS_LABEL, judgement.status)}{data.needs_rereview ? ', 재검토' : ''}</KvField>
        <div className="kv-field"><span>사유</span><div className="kv-text" style={{ minHeight: 0 }}>{judgement.reason}</div></div>
        <KvField label="미확인">{judgement.unresolved.join(', ') || '없음'}</KvField>
        <KvField label="보완 경로">{label(PATH_LABEL, judgement.path ?? '') || '없음'}</KvField>
        {judgement.rnd_check && judgement.rnd_check !== 'NOT_REQUIRED' && <KvField label="R&D 확인">{label(RND_CHECK_LABEL, judgement.rnd_check)}</KvField>}
        <KvField label="다음 담당">{label(OWNER_LABEL, judgement.next_owner_field ?? '') || '없음'}</KvField>
        <p className="kv-sub" style={{ margin: '6px 0 0' }}>상태, 성격, 조건, 사유를 바꾸려면 변경 제안. 승인된 제안만 반영</p>
      </KvBox>
    </KvSplit>
    <div className="kv-gridhead"><span>이 프로젝트 질문 {all.length}건, 회신 대기 {waiting}</span></div>
    {questions.isError
      ? <ErrorState error={questions.error} onRetry={() => questions.refetch()} />
      : <KvGrid label="이 프로젝트 질문" columns={columns} rows={rows} rowKey={q => q.question_id} fallback={questions.isPending ? <Loading /> : '질문이 없습니다.'} />}
    {confirm.isError && <Failure error={confirm.error} onReload={() => { confirm.reset(); questions.refetch(); }} messages={{ TFT_VERSION_CONFLICT: '이 질문은 이미 확인됐습니다. 다시 불러와 확인하세요.' }} />}
    <KvSplit columns="1fr">
      <KvBox title="연결">
        {connections.map(([name, text, link]) => <KvField key={name} label={name}>{text}{link && <> ({link})</>}</KvField>)}
      </KvBox>
    </KvSplit>
  </main>;
}
