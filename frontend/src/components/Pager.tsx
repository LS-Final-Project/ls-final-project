// 목록 쪽 번호. 모든 쪽 번호를 보이고(…로 줄이지 않음) 한 쪽뿐이면 숨긴다.
export default function Pager({ page, pages, onPage, label }: { page: number; pages: number; onPage: (page: number) => void; label: string }) {
  if (pages <= 1) return null;
  return <nav className="pg3" aria-label={label}>
    <button type="button" disabled={page === 0} onClick={() => onPage(page - 1)}>이전</button>
    {Array.from({ length: pages }, (_, index) => <button key={index} type="button" className={index === page ? 'on' : undefined} aria-current={index === page ? 'page' : undefined} aria-label={`${index + 1}쪽`} onClick={() => onPage(index)}>{index + 1}</button>)}
    <button type="button" disabled={page === pages - 1} onClick={() => onPage(page + 1)}>다음</button>
  </nav>;
}

// 한 쪽 크기로 나눈 행과 현재 쪽. 조건이 바뀌어 쪽 수가 줄면 마지막 쪽으로 맞춘다.
export function paged<T>(rows: T[], page: number, size: number) {
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const at = Math.min(page, pages - 1);
  return { pages, at, shown: rows.slice(at * size, at * size + size) };
}
