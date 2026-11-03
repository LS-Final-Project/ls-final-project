import type { ReactNode } from 'react';
import './log-table.css';

// 문의 처리 화면(CS0102)의 처리 기록 표와 같은 모양(.td3 .log)의 표. 셸 공통 표(kv-grid)의 가는 글씨·번호 열을 쓰지 않는다.
export type LogColumn<T> = { key: string; header: ReactNode; width?: number; className?: string; cell: (row: T, index: number) => ReactNode };

export default function LogTable<T>({ label, columns, rows, rowKey, fallback, selectedKey, onRow }: {
  label: string;
  columns: LogColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  fallback: ReactNode;
  selectedKey?: string;
  onRow?: (row: T) => void;
}) {
  return <div className="logwrap">
    <table className="log" aria-label={label}>
      <thead><tr>{columns.map(column => <th key={column.key} className={column.className} style={column.width ? { width: column.width } : undefined}>{column.header}</th>)}</tr></thead>
      <tbody>{rows.length === 0
        ? <tr><td colSpan={columns.length} className="none">{fallback}</td></tr>
        : rows.map((row, index) => <tr key={rowKey(row)} className={[onRow ? 'pick' : '', rowKey(row) === selectedKey ? 'sel' : ''].filter(Boolean).join(' ') || undefined} onClick={onRow && (() => onRow(row))}>
          {columns.map(column => <td key={column.key} className={column.className}>{column.cell(row, index)}</td>)}
        </tr>)}</tbody>
    </table>
  </div>;
}
