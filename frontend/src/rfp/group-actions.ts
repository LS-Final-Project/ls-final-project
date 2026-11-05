import type { Question, RequirementDetail, RequirementSummary } from '../api/rfp';
import { ApiError } from '../api/client';
import { errorText } from '../api/error-text';
import { attributeName, requirementNo } from './labels';

// 묶음 요약과 묶음 작업이 쓰는 판단 규칙. 요구 목록(RequirementSummary)만으로 센다. 요구별 정확한 판단은 카드가 상세로 한다.
export type Situation = 'evidence' | 'conditional' | 'gap' | 'material' | 'customer' | 'task';
export const SITUATION_LABEL: Record<Situation, string> = {
  evidence: '근거 있음', conditional: '남은 조건', gap: '사양 차이', material: '자료 부족', customer: '요구값 없음', task: '과업',
};

export function situationOf(row: RequirementSummary): Situation {
  if (row.task?.trim()) return 'task';
  if (row.status === 'EVIDENCE_FOUND') return 'evidence';
  if (row.status === 'CONDITIONAL') return 'conditional';
  if (row.status === 'SPEC_GAP') return 'gap';
  return row.value_text?.trim() ? 'material' : 'customer';
}

const decided = (row: RequirementSummary) => !!row.path && row.path !== 'NONE';

export type GroupActionKey = Situation | 'replies';
export type GroupAction = { key: GroupActionKey; title: string; next: string; rows: RequirementSummary[]; questions?: Question[] };

export function groupSummary(rows: RequirementSummary[], questions: Question[]) {
  const unique = [...new Map(rows.map(row => [row.requirement_id, row])).values()];
  const of = (situation: Situation) => unique.filter(row => situationOf(row) === situation);
  const ids = new Set(unique.map(row => row.requirement_id));
  const waiting = questions.filter(q => q.status === 'OPEN' && !!q.requirement_id && ids.has(q.requirement_id));
  const counts = {
    evidence: of('evidence').length, conditional: of('conditional').length, gap: of('gap').length,
    material: of('material').length + of('customer').length, task: of('task').length,
  };
  const noQuestion = (row: RequirementSummary) => row.open_questions === 0 && !decided(row);
  const candidates: GroupAction[] = [
    { key: 'material', title: '자료 부족', next: '기술 담당에게 자료 요청서 만들기', rows: of('material').filter(noQuestion) },
    { key: 'customer', title: '요구값 없음', next: '발주처 질의 목록 만들기', rows: of('customer').filter(noQuestion) },
    { key: 'task', title: '과업', next: '수행 주체 정하기', rows: of('task').filter(row => !decided(row)) },
    { key: 'gap', title: '사양 차이', next: '보완 방안 고르기', rows: of('gap').filter(row => !decided(row)) },
    { key: 'conditional', title: '남은 조건', next: '적용 조건 확인하기', rows: of('conditional').filter(noQuestion) },
    { key: 'evidence', title: '근거 있음', next: '적용 조건 검토 후 제안 범위에 반영', rows: of('evidence').filter(row => !decided(row)) },
  ];
  const actions = candidates.filter(action => action.rows.length > 0);
  if (waiting.length) actions.push({ key: 'replies', title: '작성한 질문', next: '받은 회신 기록하기', rows: unique.filter(row => waiting.some(q => q.requirement_id === row.requirement_id)), questions: waiting });
  return { total: unique.length, counts, actions, waiting };
}

// 질문 종류. 내부는 기술 자문(TECH_ADVICE)이고 고객은 값·범위·조건 질문이다.
export type RequestKind = 'material' | 'condition' | 'customer-value' | 'customer-scope' | 'customer-condition';
export const REQUEST_ISSUE: Record<RequestKind, Question['issue_type']> = {
  material: 'TECH_ADVICE', condition: 'TECH_ADVICE', 'customer-value': 'EMPTY_VALUE', 'customer-scope': 'SCOPE_UNCLEAR', 'customer-condition': 'CONDITION_MISSING',
};
export const isCustomerRequest = (kind: RequestKind) => kind.startsWith('customer');

export const subjectOf = (row: Pick<RequirementSummary, 'equipment' | 'attribute' | 'attribute_key' | 'task'>) =>
  [row.equipment?.trim(), attributeName(row.attribute_key, row.attribute) || row.task?.trim()].filter(Boolean).join(' · ') || '요구';
export const valueOf = (row: RequirementSummary, detail?: RequirementDetail) =>
  (detail ? [detail.spec.value, detail.spec.unit].filter(Boolean).join(' ') : '') || row.value_text?.trim() || '';
export const shortText = (text: string, max = 120) => { const value = text.replace(/\s+/g, ' ').trim(); return value.length > max ? `${value.slice(0, max)}…` : value; };

// 분석 과정의 메모라 받는 부서가 할 일이 없는 문구. 보내는 질문에는 넣지 않는다(화면의 판단 근거에는 남는다).
const PROCESS_NOTES = new Set([
  '요구 속성(사양 항목) 확인', '요구 장비 계열 확인', '수행 주체 확인(자사/협력사)', '제출 자료 준비 확인',
  '다른 자료 범위 검색 또는 담당 부서 확인',
]);

// 질문 문장. 요구 번호·장비·속성·값·인용은 보낼 내용의 항목 머리에 따로 붙으므로 여기서 되풀이하지 않는다.
export function draftQuestion(kind: RequestKind, detail?: RequirementDetail) {
  const missing = detail ? [...detail.spec.undetermined_items, ...detail.judgement.unresolved].filter(value => value && !PROCESS_NOTES.has(value.trim())) : [];
  const conditions = detail?.spec.conditions.map(c => [c.name, [c.value, c.unit].filter(Boolean).join(' ')].filter(Boolean).join(' ')) ?? [];
  const check = missing.length ? ` 확인할 것: ${missing.join(', ')}.` : '';
  switch (kind) {
    case 'material': return `자사 자료에서 근거를 찾지 못했습니다. 맞출 수 있는 제품·모델과 근거 자료(카탈로그, 시험 성적서 등)를 알려 주세요.${check}`;
    case 'condition': return `적용 조건을 맞춰 봐야 충족 여부를 알 수 있습니다.${conditions.length ? ` 적용 조건: ${conditions.join(', ')}.` : ''} 이 조건에서 자사 제품이 요구를 충족하는지와 근거 자료를 알려 주세요.${check}`;
    case 'customer-value': return '요구값이 원문에 없습니다. 적용할 값과 조건을 알려 주십시오.';
    case 'customer-scope': return '요구 범위가 원문에서 분명하지 않습니다. 요구하시는 범위와 적용 조건을 알려 주십시오.';
    case 'customer-condition': return '이 요구에 적용할 조건(사용 환경, 시험 기준 등)을 알려 주십시오.';
  }
}

// 보낼 내용: 받는 곳마다 하나의 글. 앱은 보내지 않고, 사람이 복사해 직접 전달한다.
export type OutboxItem = { no: string; subject: string; value: string; quote: string; question: string };
export function outboxText({ recipient, customer, projectName, items }: { recipient: string; customer: boolean; projectName: string; items: OutboxItem[] }) {
  const title = customer ? `[${projectName}] RFP 질의 ${items.length}건` : `[${projectName}] RFP 검토 자료 요청 ${items.length}건`;
  const intro = customer
    ? ['안녕하십니까.', `${projectName} RFP를 검토하면서 확인이 필요한 항목이 있어 질의드립니다.`]
    : ['안녕하세요.', `${projectName} RFP를 검토하면서 자사 자료로 확인하지 못한 항목이 있습니다.`, '항목별로 확인 내용과 근거 자료를 알려 주세요.'];
  const body = items.map((item, index) => [
    `${index + 1}. 요구 ${item.no} · ${item.subject}`,
    ...(item.value ? [`   고객 요구: ${item.value}`] : []),
    ...(item.quote ? [`   원문: "${shortText(item.quote)}"`] : []),
    `   ${customer ? '질의' : '질문'}: ${item.question}`,
  ].join('\n'));
  const reply = customer ? '항목 번호별로 회신해 주시면 검토에 반영하겠습니다.' : '회신하실 때 항목 번호를 함께 적어 주세요. 받은 회신은 RFP 검토 화면에서 질문별로 기록합니다.';
  return [`받는 곳: ${recipient}`, `제목: ${title}`, '', ...intro, '', body.join('\n\n'), '', reply, ''].join('\n');
}
export const outboxItem = (question: Question, row: RequirementSummary | undefined, detail?: RequirementDetail): OutboxItem => ({
  no: row ? requirementNo(row.code) : '번호 미확인', subject: row ? subjectOf(row) : '요구', value: row ? valueOf(row, detail) : '',
  quote: detail?.origin.quote ?? '', question: question.question_text,
});

// 건별 사유를 화면 문장으로. 서버 문구는 영어라 코드와 알려진 문구만 바꾸고 원문은 보이지 않는다.
const VALIDATION_TEXT: [RegExp, string][] = [
  [/review department/i, '받는 부서가 이 프로젝트의 검토 부서가 아닙니다.'],
  [/clause/i, '원문 조항이 이 프로젝트에 없습니다.'],
  [/requirement is required/i, '요구가 연결되지 않았습니다.'],
];
export function itemReason(code: string | undefined, message: string) {
  if (code === 'VALIDATION_ERROR' || !code) return VALIDATION_TEXT.find(([pattern]) => pattern.test(message))?.[1] ?? '입력값을 확인하세요.';
  return errorText(code, message);
}

// 묶음 질문 422의 건별 사유. 서버 검사는 details[{index, code, message}], 요청 형식 오류는 field "body.items.3.question_text"다.
export function batchProblems(error: unknown) {
  const byIndex = new Map<number, string>();
  const details = error instanceof ApiError && Array.isArray(error.details) ? error.details as Record<string, unknown>[] : [];
  for (const detail of details) {
    const index = detail.index !== undefined ? Number(detail.index) : Number(String(detail.field ?? '').match(/items\.(\d+)/)?.[1] ?? NaN);
    if (!Number.isInteger(index)) continue;
    const message = itemReason(typeof detail.code === 'string' ? detail.code : undefined, typeof detail.message === 'string' ? detail.message : '');
    byIndex.set(index, byIndex.has(index) ? `${byIndex.get(index)} ${message}` : message);
  }
  return byIndex;
}

// 요구별 읽기를 한꺼번에 50개 보내면 중계가 연결을 끊는다(실측). 동시에 limit개까지만 보낸다.
export async function mapLimited<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out = new Array<R>(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) { const index = next++; out[index] = await fn(items[index]); }
  }));
  return out;
}
