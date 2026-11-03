import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { PartnerCandidate } from '../api/rfp-supplement';
import { groupCounts, groupPartners, type EquipmentGroup } from './equipment-groups';

export function ReviewPages({ page, count, onChange, label }: { page: number; count: number; onChange: (page: number) => void; label: string }) {
  const last = Math.max(0, count - 1);
  const start = Math.max(0, Math.min(page - 2, count - 5));
  const pages = Array.from({ length: Math.min(5, count) }, (_, n) => start + n);
  return <nav className="rfp-review-pages" aria-label={label}>
    <button className="kb" disabled={page === 0} onClick={() => onChange(page - 1)}>이전</button>
    {start > 0 && <><button className="kb" onClick={() => onChange(0)}>1</button><span>…</span></>}
    {pages.map(n => <button className="kb" key={n} aria-current={page === n ? 'page' : undefined} onClick={() => onChange(n)}>{n + 1}</button>)}
    {pages.at(-1) !== last && count > 0 && <><span>…</span><button className="kb" onClick={() => onChange(last)}>{count}</button></>}
    <button className="kb" disabled={page >= last} onClick={() => onChange(page + 1)}>다음</button>
  </nav>;
}

export function EquipmentOverview({ groups, selected, onSelect, filtered }: { groups: EquipmentGroup[]; selected?: string; onSelect: (key: string) => void; filtered: boolean }) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const visible = groups.filter(g => `${g.name} ${g.section ?? ''}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const count = Math.ceil(visible.length / 8);
  const current = Math.min(page, Math.max(0, count - 1));
  return <div className="rfp-equipment-overview">
    <div className="rfp-equipment-heading"><div><h3>설비·장비별 대응 현황</h3><p>{filtered ? '미해결 필수 요구 기준' : '현재 문서 전체 요구 기준'} · {groups.length}개 검토 묶음 · {groups.reduce((n, g) => n + g.rows.length, 0)}개 요구</p></div><label>설비·장비 찾기<input value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} placeholder="장비명 또는 원문 절 번호" /></label></div>
    <p className="rfp-group-basis">원문 절과 정확한 장비 표기로 묶었습니다. 같은 실물 장비임을 확정하거나 서로 다른 모델의 충족 값을 합산하지 않습니다.</p>
    <div className="rfp-equipment-cards" role="group" aria-label="설비·장비 선택">
      {visible.slice(current * 8, current * 8 + 8).map(group => {
        const summary = groupCounts(group);
        return <button className="rfp-equipment-card" key={group.key} aria-pressed={selected === group.key} onClick={() => onSelect(group.key)}>
          <small>{group.kind === 'equipment' ? '장비 검토' : group.kind === 'task' ? '수행 업무' : '분류 확인 필요'} · {group.section ? `원문 절 ${group.section}` : '절 범위 미확정'}</small>
          <strong>{group.name}</strong><span>세부 요구 {group.rows.length}개</span>
          <span className="rfp-equipment-stats"><span>근거 {summary.evidence}</span><span>사양 차이 {summary.gap}</span><span>조건부 {summary.conditional}</span><span>자료 부족 {summary.unknown}</span></span>
        </button>;
      })}
      {!visible.length && <p className="rfp-review-empty">검색한 설비·장비 묶음이 없습니다.</p>}
    </div>
    {count > 1 && <ReviewPages label="설비·장비 페이지" page={current} count={count} onChange={setPage} />}
  </div>;
}

const GRADE: Record<string, string> = { MANDATORY_CONFIRMED: '필수 기술조건 확인', REVIEW: '검토 필요', REFERENCE_ONLY: '참고용', EXCLUDED: '제외' };
export function EquipmentResponse({ group, candidates, candidatesPending, candidatesError, projectId }: { group: EquipmentGroup; candidates: PartnerCandidate[]; candidatesPending: boolean; candidatesError: boolean; projectId: string }) {
  const summary = groupCounts(group);
  const partners = groupPartners(group, candidates);
  return <section className="rfp-equipment-response" aria-label="선택 설비 대응 요약">
    <header><h3>{group.name}</h3><span>{group.section ? `원문 절 ${group.section}` : '절 범위 미확정'} · {group.rows.length}개 요구</span></header>
    <div className="rfp-response-columns">
      <div><h4>자사 대응 근거</h4><strong>{summary.evidence}개 근거 있음</strong><p>사양 차이 {summary.gap} · 조건부 {summary.conditional} · 자료 부족 {summary.unknown}</p><p>항목별 판정입니다. 한 모델이 설비 전체를 충족한다는 뜻은 아닙니다.</p><Link to={`/tft/projects/${projectId}/table`}>자사 대응표 열기 →</Link></div>
      <div><h4>협력사 보완 검토</h4>{candidatesPending ? <p>저장된 후보 판정 조회 중</p> : candidatesError ? <p role="alert">후보 조회 실패 · 대응 가능 여부 미확인</p> : partners.length ? <ul>{partners.map(({ candidate, grades }) => <li key={candidate.candidate_code}><b>{candidate.display_name}</b><span>{candidate.overall_grade === 'EXCLUDED' ? '전체 후보 제외' : [...new Set(grades.map(g => GRADE[g.grade]))].join(' · ')} · 관련 요구 {grades.length}개</span></li>)}</ul> : <p>현재 요구·프로필 판본에 맞는 저장 후보 판정이 없습니다. 대응 불가 판정은 아닙니다.</p>}<p>가상 협력사 자료 · 고객 허용·R&D·통합 책임 확인 후 선택</p><Link to={`/tft/projects/${projectId}/supplement`}>보완 경로·협력사 비교 →</Link></div>
      <div><h4>남은 문제와 다음 확인</h4><p>사양 차이 {summary.gap} · 근거 부족 {summary.unknown}</p><p>조건부 {summary.conditional} · 원문/재검토 {summary.review}</p><p>열린 질문 {summary.questions} · R&D 대기 {summary.rnd}</p><p>미기재·미확인을 대응 불가로 바꾸지 않습니다. 아래 세부 요구에서 사유와 근거를 확인하세요.</p></div>
    </div>
  </section>;
}
