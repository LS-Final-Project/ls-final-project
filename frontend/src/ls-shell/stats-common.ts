import type { StatsFilters } from '../api/cs-stats';

export const emptyFilters: StatsFilters = { start: '', end: '', dept: '', customer: '' };

// 화면은 날짜(포함)를 받고, API는 현지 시각 기준 [시작, 종료)다. 종료일은 다음 날 0시로 바꿔 그날을 포함시킨다.
const pad = (value: number) => String(value).padStart(2, '0');
export function nextDayStart(day: string): string {
  const date = new Date(`${day}T00:00`);
  date.setDate(date.getDate() + 1);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T00:00`;
}
export function toApiFilters(draft: StatsFilters): StatsFilters {
  return { ...draft, start: draft.start ? `${draft.start}T00:00` : '', end: draft.end ? nextDayStart(draft.end) : '', customer: draft.customer.trim() };
}
// 시작일이 종료일보다 늦으면 안내문을, 아니면 빈 문자열을 돌려준다.
export function rangeError(draft: StatsFilters): string {
  return draft.start && draft.end && draft.start > draft.end ? '종료일은 시작일보다 빠를 수 없습니다.' : '';
}
export const timestamp = (value: string | null) => value ? new Date(value).toLocaleString('ko-KR') : '기록 없음';
