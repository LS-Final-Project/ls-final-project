import { useEffect, type CSSProperties, type ReactNode } from 'react';

// 업무 화면 공용 조각(국내 ERP 문법). 새 화면은 ls-shell.css의 kv- 클래스를 직접 쓰지 않고 이 조각을 조립한다.

// 단추에 적힌 F키가 실제로 동작하게 한다. 브라우저 기본 동작(F5 등)은 막지 않는 키만 쓴다.
export function useHotkey(key: string, action: () => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== key) return;
      // Esc는 입력 중이거나 펼친 메뉴를 닫는 뜻일 수 있어 화면을 떠나지 않는다.
      if (key === 'Escape' && ((event.target as Element | null)?.closest?.('input,textarea,select,details[open]') || document.querySelector('details[open]'))) return;
      event.preventDefault();
      action();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [key, action, enabled]);
}
export const KvKey = ({ children }: { children: ReactNode }) => <small className="kv-key" aria-hidden="true">{children}</small>;

// 제목 줄의 접힌 항목. items는 지금 못 하는 일과 그 이유 또는 추가 동작이다.
export function KvMore({ items }: { items: { label: string; note?: string; action?: () => void }[] }) {
  return <details className="kv-more">
    <summary className="kb">기타 {items.length}</summary>
    <div role="menu">{items.map(item => <button key={item.label} role="menuitem" disabled={!item.action} onClick={item.action}>{item.label}{item.note && <small>{item.note}</small>}</button>)}</div>
  </details>;
}

// 화면 제목 줄: 메뉴 경로, 화면 이름, 화면 코드, 오른쪽 버튼.
export function KvTitle({ path, title, code, children }: {
  path: string;
  title: string;
  code?: string;
  children?: ReactNode;
}) {
  return <div className="kv-title">
    <div className="kv-path">
      <span>{path}</span><span className="sep">›</span><h1>{title}</h1>
      {code && <span className="kv-code">{code}</span>}
    </div>
    {children && <div className="kv-btns">{children}</div>}
  </div>;
}

// 제목 아래 한 줄 안내. 지금 해야 할 일을 적는다.
export const KvNext = ({ label = '다음 할 일', children }: { label?: string; children: ReactNode }) =>
  <div className="kv-next"><span className="k">{label}</span><span>{children}</span></div>;

// 제목이 있는 상자. note는 제목 오른쪽의 작은 설명이다.
export function KvBox({ title, note, bodyStyle, children }: {
  title: string;
  note?: string;
  bodyStyle?: CSSProperties;
  children: ReactNode;
}) {
  return <section className="kv-box">
    <h2>{title}{note && <small>{note}</small>}</h2>
    <div className="body" style={bodyStyle}>{children}</div>
  </section>;
}

// 나란한 상자. columns는 grid-template-columns 값이다.
export const KvSplit = ({ columns, children }: { columns?: string; children: ReactNode }) =>
  <div className="kv-split" style={columns ? { gridTemplateColumns: columns } : undefined}>{children}</div>;

// 이름표와 값 한 줄.
export const KvField = ({ label, children }: { label: string; children: ReactNode }) =>
  <div className="kv-field"><span>{label}</span><div className="ro">{children}</div></div>;

// 조회조건 격자. 자식은 KvCondRow이고, Enter나 조회 단추로 onSearch가 불린다.
export const KvCond = ({ onSearch, children }: { onSearch?: () => void; children: ReactNode }) =>
  <form className="kv-cond" aria-label="조회조건" onSubmit={event => { event.preventDefault(); onSearch?.(); }}>{children}</form>;
export const KvCondRow = ({ children, columns = 4 }: { children: ReactNode; columns?: number }) =>
  <div className="row" style={{ gridTemplateColumns: `repeat(${columns},1fr)` }}>{children}</div>;
export const KvCondField = ({ label, children }: { label: string; children: ReactNode }) =>
  <label><span>{label}</span>{children}</label>;

// 표 아래 줄: 왼쪽 건수, 가운데 이동, 오른쪽 표시 개수.
export const KvFoot = ({ count, more, size }: { count: ReactNode; more?: ReactNode; size?: ReactNode }) =>
  <div className="kv-foot"><span>{count}</span><span className="more">{more}</span>{size}</div>;

// 표시한 행을 CSV로 내려받는다(엑셀 단추). 서버 내보내기 경로가 없는 화면용이다. 엑셀이 한글을 읽도록 BOM을 붙인다.
export function downloadCsv(name: string, head: string[], rows: unknown[][]) {
  // 셀 값은 RFP 문서에서 온다. 수식으로 시작하면 엑셀이 실행하지 않게 앞에 '를 붙인다.
  const safe = (text: string) => /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  const csv = [head, ...rows].map(row => row.map(cell => `"${safe(String(cell ?? '')).replaceAll('"', '""')}"`).join(',')).join('\r\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }));
  link.download = `${name}.csv`;
  link.click();
  // 바로 해제하면 내려받기가 실패하는 브라우저가 있다.
  setTimeout(() => URL.revokeObjectURL(link.href));
}

// 이름표·값이 번갈아 나오는 띠. cells는 [이름, 값]이고 columns칸씩 줄을 바꾼다.
export const KvRibbon = ({ cells, columns = 4 }: { cells: [string, ReactNode][]; columns?: number }) =>
  <div className="kv-ribbon" style={{ gridTemplateColumns: `repeat(${columns},1fr)` }}>
    {cells.map(([name, value]) => <div key={name}><span>{name}</span><b>{value}</b></div>)}
  </div>;

export type KvColumn<T> = {
  key: string;
  header: ReactNode;
  // 고정 폭(px). 없으면 남는 폭을 나눠 쓴다.
  width?: number;
  // c 가운데, n 숫자, t 제목 등 kv-grid 셀 클래스.
  className?: string;
  cell: (row: T, index: number) => ReactNode;
};

// 표. label은 스크린 리더용 이름이고, rows가 비면 fallback을 표 안에 보여 준다.
// frozen은 앞 세 열(선택·번호·문서번호)을 고정하는 C/S 목록 배치이고 기본은 고정하지 않는다.
export function KvGrid<T>({ label, columns, rows, rowKey, fallback, frozen = false, selectedKey, onRow, scroll = false, rowClass }: {
  label: string;
  columns: KvColumn<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  fallback: ReactNode;
  frozen?: boolean;
  // 고른 행을 표시한다. 행을 고르는 동작은 셀 안의 버튼이 맡는다(키보드 접근).
  selectedKey?: string;
  // 행 어디를 눌러도 고른다. 키보드는 셀 안의 링크가 맡는다.
  onRow?: (row: T) => void;
  // 12행 높이로 자르고 표 안에서 스크롤한다(아래 패널이 한 화면에 들어오게).
  scroll?: boolean;
  rowClass?: (row: T) => string | undefined;
}) {
  return <div className={scroll ? 'kv-tablewrap scroll' : 'kv-tablewrap'}>
    <table className={frozen ? 'kv-grid' : 'kv-grid free'} aria-label={label}>
      <colgroup>{columns.map(c => <col key={c.key} style={c.width ? { width: c.width } : undefined} />)}</colgroup>
      <thead><tr>{columns.map(c => <th key={c.key} className={c.className}>{c.header}</th>)}</tr></thead>
      <tbody>
        {rows.length === 0
          ? <tr><td colSpan={columns.length}>{fallback}</td></tr>
          : rows.map((row, index) => <tr key={rowKey(row)} className={rowKey(row) === selectedKey ? 'sel' : rowClass?.(row)} onClick={onRow && (() => onRow(row))}>
            {columns.map(c => <td key={c.key} className={c.className}>{c.cell(row, index)}</td>)}
          </tr>)}
      </tbody>
    </table>
  </div>;
}
