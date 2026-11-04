import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import {
  confirmQuestion, recompareRequirement, getRequirement, listProposals, listClauses, listDocuments, listQuestions, listRequirements,
  type Clause, type Question, type RequirementDetail, type RequirementFilters,
} from '../api/rfp';
import { getProject } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime } from '../components/tft';
import { KvBox, KvField, KvGrid, KvKey, KvMore, KvNext, KvRibbon, KvSplit, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { attributeName, DATA_ORIGIN_LABEL, DEPARTMENT_LABEL, ISSUE_LABEL, label, NECESSITY_LABEL, OPERATOR_LABEL, OWNER_LABEL, PATH_LABEL, QUESTION_STATUS_LABEL, requirementNo, RND_CHECK_LABEL, STATUS_LABEL } from './labels';
import { Failure, settled, useAttemptKey } from './proposal-shared';
import ProposalForm from './ProposalForm';
import { QuestionForm, ReplyForm, ReplyRecord } from './QuestionForms';

const WALKS: Record<string, { name: string; filters: RequirementFilters }> = {
  all: { name: '전체', filters: {} },
  unresolved: { name: '미해결 필수만', filters: { unresolved_mandatory: true } },
  rereview: { name: '재검토 필요만', filters: { needs_rereview: true } },
  open: { name: '열린 질문만', filters: { has_open_question: true } },
};

// 순회 띠. 조회 결과를 끝까지 읽어 앞뒤 요구를 찾는다.
function Walk({ projectId, id, onNext }: { projectId: string; id: string; onNext: (to: string | undefined) => void }) {
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
  const path = (row: typeof after) => row && `/tft/projects/${projectId}/requirements/${row.requirement_id}`;
  const target = path(after);
  useEffect(() => { onNext(target); }, [onNext, target]);
  const go = (row: typeof rows[number] | undefined, text: string) => row
    ? <Link className="kb" to={path(row) as string}>{text} {requirementNo(row.code)}</Link>
    : <button className="kb" disabled>{text}</button>;
  return <div className="kv-walk" role="group" aria-label="순회">
    <b>순회 {list.isError ? '목록을 읽지 못했습니다' : list.isPending ? '읽는 중' : at >= 0 ? `${WALKS[walk].name.replace('만', '')} ${rows.length}건 중 ${at + 1}번째` : `조건에 맞는 요구 ${rows.length}건, 이 요구는 없음`}</b>
    <select aria-label="순회 조건" value={walk} onChange={event => setWalk(event.target.value)}>
      {Object.entries(WALKS).map(([code, { name }]) => <option key={code} value={code}>{name}</option>)}
    </select>
    <span>RFP 검토에서 대상을 고른 뒤 요구사항을 확인할 수 있습니다</span>
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
  // 요구의 조항이 뒤쪽 쪽에 있으면 찾을 때까지 읽어 목록에서 보이게 한다.
  const found = rows.some(clause => clause.clause_id === origin.clause_id);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = clauses;
  useEffect(() => {
    if (origin.clause_id && !found && hasNextPage && !isFetchingNextPage) fetchNextPage();
  }, [origin.clause_id, found, hasNextPage, isFetchingNextPage, fetchNextPage]);
  const current = useRef<HTMLButtonElement>(null);
  useEffect(() => { current.current?.scrollIntoView?.({ block: 'nearest' }); }, [found]);
  const number = picked ? picked.clause_no : origin.clause_no ?? '조항 없음';
  const text = picked ? picked.text : origin.quote;
  return <KvBox title="원문 조항" note={`조항 ${rows.length}건${clauses.hasNextPage ? ' 이상' : ''}, ${picked ? '현행 문구' : '요구가 인용한 문구'}`}>
    <div className="kv-tree">
      {clauses.isError
        ? <ErrorState error={clauses.error} onRetry={() => clauses.refetch()} />
        : clauses.isPending ? <Loading /> : rows.length === 0 ? '조항이 없습니다.' : <ul>
          {rows.map(clause => <li key={clause.clause_id}>
            <button
              ref={clause.clause_id === origin.clause_id ? current : undefined}
              aria-current={clause.clause_id === (picked?.clause_id ?? origin.clause_id) ? 'true' : undefined}
              style={{ paddingLeft: 8 + (clause.depth - 1) * 12 }}
              onClick={() => setPicked(clause)}
            ><b>{clause.clause_no}</b>{clause.title ?? ''}<small>{clause.page != null ? `p.${clause.page}` : ''}</small></button>
          </li>)}
        </ul>}
      {clauses.hasNextPage && <button className="kb" disabled={clauses.isFetchingNextPage} onClick={() => clauses.fetchNextPage()}>조항 더 보기</button>}
    </div>
    <div className="kv-text kv-clause" lang="en"><b>{number}{(picked?.page ?? origin.page) != null ? `, p.${picked?.page ?? origin.page}` : ''}</b>{text}</div>
    <Deleted values={picked ? picked.deleted_values : origin.deleted_values} />
    {picked && picked.clause_id !== origin.clause_id && <button className="kb" style={{ marginTop: 6 }} onClick={() => setPicked(undefined)}>요구 원문으로</button>}
  </KvBox>;
}

type Connection = { name: string; text: string; to?: string };
const CONNECTIONS: KvColumn<Connection>[] = [
  { key: 'name', header: '연결', width: 80, cell: c => c.name },
  { key: 'text', header: '내용', className: 't', cell: c => c.text },
  { key: 'to', header: '상태', width: 52, className: 'c', cell: c => c.to ? <Link to={c.to}>보기</Link> : '' },
];

type Panel = { kind: 'propose'; questionId?: string } | { kind: 'read-reply'; id: string } | { kind: 'question' } | { kind: 'reply'; id: string };

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
  const [proposalSaved, setProposalSaved] = useState(false);
  const [replyOutcome, setReplyOutcome] = useState<{ question: Question; proposed: boolean }>();
  const [nextPath, setNextPath] = useState<string>();
  const navigate = useNavigate();
  const requirement = useQuery({ queryKey: ['rfp-requirement', mode, id], queryFn: () => getRequirement(id), enabled: access.ready });
  const questions = useQuery({ queryKey: ['rfp-questions', mode, projectId], queryFn: () => listQuestions(projectId), enabled: access.ready });
  const proposals = useQuery({ queryKey: ['rfp-proposals', mode, projectId, id], queryFn: () => listProposals(projectId, { requirement_id: id }), enabled: access.ready });
  const project = useQuery({ queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled: access.ready });
  const documents = useQuery({ queryKey: ['rfp-documents', mode, projectId], queryFn: () => listDocuments(projectId), enabled: access.ready });
  const propose = useCallback(() => setPanel({ kind: 'propose' }), []);
  const back = useCallback(() => navigate(`/tft/projects/${projectId}#rfp-document-review`), [navigate, projectId]);
  useHotkey('F6', propose, access.ready);
  useHotkey('Escape', back, access.ready && !panel);
  const reload = () => {
    for (const key of ['rfp-questions', 'rfp-requirement', 'rfp-requirements', 'rfp-proposals', 'projects']) client.invalidateQueries({ queryKey: [key, mode] });
  };
  const confirm = useMutation({
    // ponytail: 시도마다 새 키다. 확인은 DRAFT에서만 되므로 재전송은 409로 끝난다.
    mutationFn: (question: Question) => confirmQuestion(projectId, question.question_id, crypto.randomUUID()),
    onSuccess: reload,
  });
  const [recompareKey, nextRecompareKey] = useAttemptKey(`${id}:${requirement.data?.data.version}`);
  const recompare = useMutation({
    mutationFn: () => recompareRequirement(id, requirement.data!.data.version, recompareKey),
    onError: error => { if (settled(error)) nextRecompareKey(); },
    onSuccess: result => { nextRecompareKey(); setNotice(result.data.proposal ? '저장된 자료로 다시 비교했습니다. 비교 결과로 만든 변경 제안을 검토하세요.' : '저장된 자료로 다시 비교했습니다. 비교 결과와 남은 확인 사항을 검토하세요.'); reload(); },
  });
  if (!access.ready) return <BusinessGate access={access} title="RFP 검토" />;
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
  const no = requirementNo(data.code);
  const attribute = attributeName(spec.attribute_key, spec.attribute);
  const written = judgement.provenance_kind === 'ai_draft' ? 'AI 작성' : '직접 입력';
  const document = documents.data?.data.items.find(d => d.document_version_id === data.origin.document_version_id);
  const documentId = document?.document_id;
  const pending = links.pending_proposal_ids.length;
  const received = all.filter(q => q.requirement_id === id && q.status === 'REPLIED');
  const nextStep = [
    pending > 0 && `변경 제안 ${pending}건 승인`,
    pending === 0 && received.length > 0 && '받은 회신 읽고 반영 검토',
    received.length === 0 && links.question_ids.length > 0 && `질문 ${links.question_ids.length}건 확인`,
  ].filter(Boolean).join(', ') || '변경 제안 또는 질문 작성';
  const columns: KvColumn<Question>[] = [
    { key: 'requirement', header: '요구', width: 58, className: 'c', cell: q => q.requirement_id === id ? '이 요구' : q.requirement_id ? <Link to={`/tft/projects/${projectId}/requirements/${q.requirement_id}`}>보기</Link> : '없음' },
    { key: 'issue', header: '유형', width: 72, className: 'c', cell: q => label(ISSUE_LABEL, q.issue_type) },
    { key: 'target', header: '대상', width: 60, className: 'c', cell: q => q.target === 'INTERNAL' ? label(DEPARTMENT_LABEL, q.target_dept) : '고객' },
    { key: 'text', header: '질문 내용', className: 't', cell: q => q.question_text },
    { key: 'status', header: '상태', width: 66, className: 'c', cell: q => label(QUESTION_STATUS_LABEL, q.status) },
    { key: 'reply', header: '회신', width: 120, cell: q => q.reply_received_at ? formatDateTime(q.reply_received_at) : q.internal_assumption ? `가정 ${q.internal_assumption.value}` : '' },
    { key: 'action', header: '처리', width: 76, className: 'c', cell: q => {
      if (q.status === 'DRAFT') return <button className="kb sm" disabled={confirm.isPending} title="초안을 확인해 보낸 질문으로 바꿉니다" onClick={() => confirm.mutate(q)}>질문 확인</button>;
      if (q.status === 'OPEN') return <button className="kb sm" onClick={() => { setReplyOutcome(undefined); setNotice(undefined); setPanel({ kind: 'reply', id: q.question_id }); }}>회신 기록</button>;
      if (q.status === 'REPLIED') return <button className="kb sm" onClick={() => setPanel({ kind: 'read-reply', id: q.question_id })}>회신 읽기</button>;
      return '';
    } },
  ];
  const toProject = `/tft/projects/${projectId}`;
  const connections: Connection[] = [
    { name: '자사 비교', text: links.comparison_summary ?? '없음', to: `${toProject}#rfp-document-review` },
    { name: '보완 경로', text: label(PATH_LABEL, links.supplement_summary) || '없음', to: `${toProject}/supplement` },
    { name: '협력사 후보', text: `${links.partner_candidate_ids.length}건` },
    { name: '질문', text: `${links.question_ids.length}건` },
    { name: '승인 대기', text: `${pending}건`, to: pending > 0 ? `${toProject}/table/approvals` : undefined },
    { name: '초안 문장', text: `${links.draft_sentence_ids.length}건`, to: `${toProject}/draft` },
  ];
  return <main>
    <KvTitle path="제안 지원" title="RFP 검토">
      <button className="kb primary" onClick={propose}>변경 제안<KvKey>F6</KvKey></button>
      <button className="kb" onClick={() => setPanel({ kind: 'question' })}>질문 작성</button>
      {nextPath ? <Link className="kb" to={nextPath}>다음 요구</Link> : <button className="kb" disabled>다음 요구</button>}
      {documentId
        ? <a className="kb" href={`/api/v1/tft/documents/${encodeURIComponent(documentId)}/file`}>원문 내려받기</a>
        : <button className="kb" disabled>원문 내려받기</button>}
      <Link className="kb" to={`/tft/projects/${projectId}#rfp-document-review`}>RFP 검토<KvKey>Esc</KvKey></Link>
      <KvMore items={[
        { label: '상태 직접 수정', note: '승인 전 반영 금지, 변경 제안으로' },
        { label: '처리 실행', note: '요구 화면에서는 실행하지 않음' },
      ]} />
    </KvTitle>
    <KvNext>요구 {[no, attribute, label(NECESSITY_LABEL, spec.necessity), label(STATUS_LABEL, judgement.status), data.needs_rereview && '재검토 필요'].filter(Boolean).join(', ')}. 다음 {nextStep}. 재실행이 판단을 덮어쓰지 않도록 승인된 판단만 반영</KvNext>
    {data.workflow && (data.workflow.comparison_stale || data.workflow.supplement_stale || (data.workflow.draft_sentence_ids ?? []).length > 0) && <KvBox title="요구 변경 후 확인할 것">
      {data.workflow.comparison_stale && <p>요구가 바뀌어 이전 자사 비교를 다시 확인해야 합니다. <button className="kb primary" disabled={recompare.isPending} onClick={() => recompare.mutate()}>저장된 자료로 다시 비교</button></p>}
      {data.workflow.supplement_stale && <p>기존 대응 방안이 변경된 요구에도 맞는지 확인하세요. <Link to={`${toProject}/supplement`}>대응 방안 검토</Link></p>}
      {(data.workflow.draft_sentence_ids ?? []).length > 0 && <p>연결된 초안 {(data.workflow.draft_sentence_ids ?? []).length}문장에 확인이 필요합니다. <Link to={`${toProject}/draft`}>초안에서 확인</Link></p>}
      {recompare.isSuccess && recompare.data.data.proposal && <Link className="kb primary" to={`${toProject}/table/approvals`}>재비교 변경 제안 검토</Link>}
      {recompare.isError && <Failure error={recompare.error} onReload={reload} messages={{ TFT_RECOMPARE_INPUT_INCOMPLETE: '이전 분석에 비교 입력이 충분히 남아 있지 않습니다. 원문과 자사 자료를 확인해 변경을 제안하세요.' }} />}
    </KvBox>}
    <Walk projectId={projectId} id={id} onNext={setNextPath} />
    <KvRibbon cells={[
      ['프로젝트', project.data?.data.external_id ?? project.data?.data.name ?? ''],
      ['문서', document ? `${document.title} v${document.version}` : ''],
      ['조항', [data.origin.clause_no, data.origin.page != null ? `p.${data.origin.page}` : ''].filter(Boolean).join(', ')],
      ['편집 흔적', data.origin.edit_trace_support ? '확인 가능' : '확인 불가'],
    ]} />
    {proposalSaved && <div className="kv-actions" style={{ margin: "8px 14px" }}><Link className="kb primary" to={`${toProject}/table/approvals`}>저장한 변경 제안 검토</Link></div>}
    {notice && <p role="status" style={{ margin: '6px 14px 0' }}>{notice}</p>}
    {replyOutcome && panel?.kind !== 'propose' && !(panel?.kind === 'read-reply' && all.some(q => q.question_id === panel.id && q.reply)) && <div className="kv-actions" style={{ margin: '6px 14px' }}>
      {replyOutcome.proposed ? <Link className="kb primary" to={`${toProject}/table/approvals`}>회신으로 생긴 변경 제안 검토</Link>
        : replyOutcome.question.requirement_id === id ? <>
          <button type="button" className="kb primary" onClick={propose}>근거 확인 후 판정 변경 제안</button>
          {replyOutcome.question.issue_type === 'RND_FEASIBILITY' && <Link className="kb" to={`${toProject}/supplement`}>개발 회신에 맞는 대응 방안 검토</Link>}
        </> : replyOutcome.question.requirement_id ? <Link className="kb primary" to={`${toProject}/requirements/${replyOutcome.question.requirement_id}`}>회신이 연결된 요구 검토</Link>
          : <span>연결된 요구가 없는 질문입니다. 관련 요구를 찾아 회신 내용을 대조하세요.</span>}
    </div>}
    {panel?.kind === 'read-reply' && all.filter(q => q.question_id === panel.id).map(q => <ReplyRecord key={q.question_id} question={q} proposals={proposals.data?.data.items} proposalsLoading={proposals.isPending} onPending={() => navigate(`${toProject}/table/approvals`)} onReview={q.requirement_id === id ? () => q.issue_type === 'RND_FEASIBILITY' ? navigate(`${toProject}/supplement`) : setPanel({ kind: 'propose', questionId: q.question_id }) : undefined} />)}
    {panel?.kind === 'propose' && <ProposalForm
      key={panel.questionId ?? 'manual'}
      sourceQuestion={all.find(q => q.question_id === panel.questionId)}
      projectId={projectId}
      requirement={data}
      onCancel={() => setPanel(undefined)}
      onSaved={() => { setPanel(undefined); setProposalSaved(true); setNotice('변경 제안을 저장했습니다. 승인 전에는 요구가 바뀌지 않습니다.'); reload(); }}
      onReload={() => requirement.refetch()}
    />}
    {panel?.kind === 'question' && <QuestionForm projectId={projectId} requirement={data} departments={project.data?.data.review_departments ?? []} onCancel={() => setPanel(undefined)} onSaved={() => { setPanel(undefined); reload(); }} />}
    {replying && <ReplyForm
      key={replying.question_id}
      question={replying}
      onCancel={() => setPanel(undefined)}
      onReload={() => questions.refetch()}
      onSaved={proposed => {
        setPanel({ kind: 'read-reply', id: replying.question_id });
        setReplyOutcome({ question: replying, proposed });
        setNotice(replying.target === 'INTERNAL' ? replying.issue_type === 'RND_FEASIBILITY' ? '개발 회신을 기록했습니다. 가능 여부와 일정 조건을 확인한 뒤 대응 방안을 검토하세요. 요구 충족 판정은 자동으로 바뀌지 않습니다.' : '회신을 기록했습니다. 회신과 근거 자료를 대조한 뒤 판정 변경 여부를 검토하세요. 판정은 자동으로 바뀌지 않습니다.' : proposed ? '회신을 기록했습니다. 변경 제안이 생겼고 승인 전에는 요구가 바뀌지 않습니다.' : '회신을 기록했습니다. 내용을 읽고 요구에 반영할 항목을 선택하세요.');
        reload();
      }}
    />}
      <div>
        <p className="kv-label">이 프로젝트 질문 {all.length}건, 회신 대기 {waiting}</p>
        {questions.isError
          ? <ErrorState error={questions.error} onRetry={() => questions.refetch()} />
          : <KvGrid label="이 프로젝트 질문" columns={columns} rows={rows} rowKey={q => q.question_id} scroll fallback={questions.isPending ? <Loading /> : '질문이 없습니다.'} />}
        {confirm.isError && <Failure error={confirm.error} onReload={() => { confirm.reset(); questions.refetch(); }} messages={{ TFT_VERSION_CONFLICT: '이 질문은 이미 확인됐습니다. 다시 불러와 확인하세요.' }} />}
      </div>
    <KvSplit columns="1.28fr 1fr 1fr">
      <Source key={data.origin.document_version_id} data={data} />
      <KvBox title="요구" note={written}>
        <KvField label="장비, 작업">{[spec.equipment, spec.task].filter(Boolean).join(', ') || '없음'}</KvField>
        <KvField label="속성">{attribute || '없음'}</KvField>
        <KvField label="요구값">{value || '없음'}</KvField>
        <KvField label="조건">{spec.conditions.map(c => [c.name, c.value, c.unit].filter(Boolean).join(' ')).join(', ') || '없음'}</KvField>
        <KvField label="규격">{spec.standard_system === 'NONE' ? '없음' : spec.standard_system}</KvField>
        <KvField label="성격">{label(NECESSITY_LABEL, spec.necessity)}</KvField>
        <KvField label="지정 모델">{spec.designated_model ?? '없음'}</KvField>
        <KvField label="미확정 항목">{spec.undetermined_items.join(', ') || '없음'}</KvField>
        {spec.attribute && attribute !== spec.attribute && <KvField label="원문 속성">{spec.attribute}</KvField>}
      </KvBox>
      <KvBox title="판단" note={written}>
        <KvField label="상태">{label(STATUS_LABEL, judgement.status)}{data.needs_rereview ? ', 재검토' : ''}{data.needs_review ? ', 원문 확인' : ''}</KvField>
        <div className="kv-field"><span>사유</span><div className="kv-text" style={{ minHeight: 0 }}>{judgement.reason}</div></div>
        <KvField label="미확인">{judgement.unresolved.join(', ') || '없음'}</KvField>
        <KvField label="보완 경로">{label(PATH_LABEL, judgement.path ?? '') || '없음'}</KvField>
        {judgement.rnd_check && judgement.rnd_check !== 'NOT_REQUIRED' && <KvField label="R&D 확인">{label(RND_CHECK_LABEL, judgement.rnd_check)}</KvField>}
        <KvField label="다음 담당">{label(OWNER_LABEL, judgement.next_owner_field ?? '') || '없음'}</KvField>
        <KvField label="판본">{data.version}, 자료 구분 {label(DATA_ORIGIN_LABEL, data.data_origin)}</KvField>
        <p className="kv-sub" style={{ margin: '6px 0 0' }}>상태, 성격, 조건, 사유를 바꾸려면 변경 제안. 승인된 제안만 반영</p>

      </KvBox>
    </KvSplit>
    <KvSplit columns="1fr">
      <div>
        <p className="kv-label">연결</p>
        <KvGrid label="연결" columns={CONNECTIONS} rows={connections} rowKey={c => c.name} fallback="연결 항목이 없습니다." />
      </div>
    </KvSplit>
  </main>;
}
