import type { CSSProperties, ReactNode } from 'react';

// 업무 화면 공용 조각(국내 ERP 문법). 새 화면은 ls-shell.css의 kv- 클래스를 직접 쓰지 않고 이 조각을 조립한다.

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
export function KvGrid<T>({ label, columns, rows, rowKey, fallback, frozen = false, selectedKey }: {
  label: string;
  columns: KvColumn<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  fallback: ReactNode;
  frozen?: boolean;
  // 고른 행을 표시한다. 행을 고르는 동작은 셀 안의 버튼이 맡는다(키보드 접근).
  selectedKey?: string;
}) {
  return <div className="kv-tablewrap">
    <table className={frozen ? 'kv-grid' : 'kv-grid free'} aria-label={label}>
      <colgroup>{columns.map(c => <col key={c.key} style={c.width ? { width: c.width } : undefined} />)}</colgroup>
      <thead><tr>{columns.map(c => <th key={c.key} className={c.className}>{c.header}</th>)}</tr></thead>
      <tbody>
        {rows.length === 0
          ? <tr><td colSpan={columns.length}>{fallback}</td></tr>
          : rows.map((row, index) => <tr key={rowKey(row)} className={rowKey(row) === selectedKey ? 'sel' : undefined}>
            {columns.map(c => <td key={c.key} className={c.className}>{c.cell(row, index)}</td>)}
          </tr>)}
      </tbody>
    </table>
  </div>;
}
