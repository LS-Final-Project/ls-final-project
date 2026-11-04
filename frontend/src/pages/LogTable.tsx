import type { ReactNode } from 'react';
import './log-table.css';

// 문의 처리 화면(CS0102)의 처리 기록 표와 같은 모양(.td3 .log)의 표. 셸 공통 표(kv-grid)의 가는 글씨·번호 열을 쓰지 않는다.
// sortValue가 있는 칸은 머리를 눌러 정렬한다(문의 조회와 같은 ▲▼ 표시). 정렬 자체는 부르는 화면이 한다.
export type LogColumn<T> = { key: string; header: ReactNode; width?: number; className?: string; cell: (row: T, index: number) => ReactNode; sortValue?: (row: T) => string | number };
export type LogSort = { key: string; dir: 'asc' | 'desc' };
export function sortRows<T>(rows: T[], columns: LogColumn<T>[], sort: LogSort | undefined): T[] {
  const value = sort && columns.find(column => column.key === sort.key)?.sortValue;
  if (!sort || !value) return rows;
  const sign = sort.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => { const x = value(a); const y = value(b); return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'ko')) * sign; });
}

export default function LogTable<T>({ label, columns, rows, rowKey, fallback, selectedKey, onRow, sort, onSort }: {
  label: string;
  columns: LogColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  fallback: ReactNode;
  selectedKey?: string;
  onRow?: (row: T) => void;
  sort?: LogSort;
  onSort?: (key: string) => void;
}) {
  const head = (column: LogColumn<T>) => {
    if (!onSort || !column.sortValue) return column.header;
    const on = sort?.key === column.key;
    return <button type="button" className="sorter" onClick={() => onSort(column.key)}
      aria-label={`${typeof column.header === 'string' ? column.header : column.key} ${on ? (sort!.dir === 'asc' ? '오름차순, 눌러서 내림차순' : '내림차순, 눌러서 오름차순') : '정렬'}`}>{column.header}</button>;
  };
  return <div className="logwrap">
    <table className="log" aria-label={label}>
      <thead><tr>{columns.map(column => <th key={column.key} className={[column.className, onSort && column.sortValue ? 'sortable' : ''].filter(Boolean).join(' ') || undefined} aria-sort={onSort && column.sortValue ? (sort?.key === column.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none') : undefined} style={column.width ? { width: column.width } : undefined}>{head(column)}</th>)}</tr></thead>
      <tbody>{rows.length === 0
        ? <tr><td colSpan={columns.length} className="none">{fallback}</td></tr>
        : rows.map((row, index) => <tr key={rowKey(row)} className={[onRow ? 'pick' : '', rowKey(row) === selectedKey ? 'sel' : ''].filter(Boolean).join(' ') || undefined} onClick={onRow && (() => onRow(row))}>
          {columns.map(column => <td key={column.key} className={column.className}>{column.cell(row, index)}</td>)}
        </tr>)}</tbody>
    </table>
  </div>;
}
