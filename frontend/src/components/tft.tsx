import type { RfpRequirement } from '../api/tft';

// docs/tft-screens.md 용어와 상태값. 코드값은 docs/openapi/tft.json의 enum을 그대로 쓰고 화면 라벨만 붙인다.
export const ROLE_STATUS_LABEL: Record<string, string> = {
  DRAFTING: '작성 중', ISSUED: '공지', RECOMMENDATIONS_RECEIVED: '후보 추천 접수', DEPT_VERIFIED: '부서 검증',
  COMPARING: '후보 비교', IN_COMPOSITION: '구성안 검토', CONFIRMED: '확정',
};
export const PROJECT_STATUS_LABEL: Record<string, string> = { INITIAL_REVIEW: '초기검토', STAFFING: '인력 요청', COMPOSING: '구성안 작성', CONFIRMED: '확정' };
export const REQUEST_STATUS_LABEL: Record<string, string> = { DRAFT: '작성 중', ISSUED: '발행', ANSWERED: '회신', CLOSED: '종료' };
export const COMPETENCY_KIND_LABEL: Record<string, string> = { REQUIRED: '필수', PREFERRED: '우대' };
export const PARTICIPATION_LABEL: Record<string, string> = { LEAD: '주관', SUPPORT: '협업' };

export const StatusBadge = ({ code, labels }: { code: string; labels: Record<string, string> }) =>
  <span className="badge" data-status={code}>{labels[code] ?? code} <small>{code}</small></span>;

// 출처 표기: 코드값을 그대로 보이고, data_origin이 real이 아니거나 ASSUMED이면 "가상 시연"을 덧붙인다(tft-screens 라벨 표).
export function Origin({ dataOrigin, derivationKind }: { dataOrigin: string; derivationKind: string }) {
  const demo = dataOrigin !== 'real' || derivationKind === 'ASSUMED';
  const label = derivationKind === 'PROVIDED' ? '원문 사실' : derivationKind === 'DERIVED' ? '기획상 도출' : null;
  return <span className="origin">{label && <small>{label}</small>}{demo && <small>가상 시연</small>}<small>{derivationKind}</small><small>{dataOrigin}</small></span>;
}

export const formatDateTime = (value: string | null | undefined) => value ? new Date(value).toLocaleString('ko-KR') : '미정';
export const formatDate = (value: string | null | undefined) => value ? value : '미정';

// 원문 근거: 문서명·판본·페이지·조항·발췌. 네 가지가 모두 없으면 "근거 없음"으로 명시하고 값을 만들지 않는다.
export function Evidence({ requirement }: { requirement: Pick<RfpRequirement, 'document_name' | 'document_version_id' | 'page' | 'clause' | 'excerpt'> }) {
  const { document_name, document_version_id, page, clause, excerpt } = requirement;
  if (!document_name && page == null && !clause && !excerpt) return <span className="evidence">근거 없음</span>;
  return <span className="evidence">
    <span>{document_name ?? '문서명 없음'}</span>
    <span>{document_version_id ? `판본 ${document_version_id}` : '판본 없음'}</span>
    <span>{page != null ? `p.${page}` : '페이지 없음'}</span>
    <span>{clause ? `§${clause}` : '조항 없음'}</span>
    {excerpt ? <q lang="en">{excerpt}</q> : <span>발췌 없음</span>}
  </span>;
}
