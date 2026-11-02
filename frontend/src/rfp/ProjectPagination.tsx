import './ProjectPagination.css';

export function ProjectPagination({ page, total, busy, onPage }: {
  page: number; total?: number | null; busy: boolean; onPage: (page: number) => void;
}) {
  if (total == null) return null;
  const pages = Math.max(1, Math.ceil(total / 10));
  const start = Math.max(1, Math.min(page - 2, pages - 4));
  const numbers = Array.from({ length: Math.min(5, pages) }, (_, i) => start + i);
  const go = (value: number) => onPage(Math.max(1, Math.min(value, pages)));
  return <nav className="rfp-project-pagination" aria-label="프로젝트 목록 페이지">
    <span>총 {total}건 · 한 페이지 10건 · {page}쪽</span>
    <div>
      <button className="kb" disabled={busy || page === 1} onClick={() => go(page - 1)}>이전</button>
      {start > 1 && <><button className="kb" disabled={busy} onClick={() => go(1)}>1</button><span>…</span></>}
      {numbers.map(value => <button key={value} className={`kb${value === page ? ' primary' : ''}`} aria-label={`${value}페이지`} aria-current={value === page ? 'page' : undefined} disabled={busy} onClick={() => go(value)}>{value}</button>)}
      {start + numbers.length <= pages && <><span>…</span><button className="kb" disabled={busy} onClick={() => go(pages)}>{pages}</button></>}
      <button className="kb" disabled={busy || page >= pages} onClick={() => go(page + 1)}>다음</button>
    </div>
  </nav>;
}

export function ProjectSearchField({ value, onChange }: {
  value: 'all' | 'external_id' | 'name'; onChange: (value: 'all' | 'external_id' | 'name') => void;
}) {
  return <select aria-label="검색 기준" value={value} onChange={e => onChange(e.target.value as typeof value)}>
    <option value="all">전체</option><option value="external_id">프로젝트 번호</option><option value="name">프로젝트명</option>
  </select>;
}
