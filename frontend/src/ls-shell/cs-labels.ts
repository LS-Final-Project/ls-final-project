import type { TicketListResponse } from '../api/cs';
import type { ImprovementList } from '../api/cs-improvements';

// C/S 화면의 표시 이름. 캔버스 보드(문의 조회 CS0101, 개선요청)의 문구를 따른다.
export type TicketState = TicketListResponse['data']['items'][number]['workflow_state'];
export type TicketChannel = TicketListResponse['data']['items'][number]['channel'];
export type RequestSummary = ImprovementList['data']['items'][number];

export const STATE_LABEL: Record<TicketState, string> = {
  RECEIVED: '접수',
  IN_REVIEW: '검토중',
  WAITING_CUSTOMER: '고객 회신 대기',
  ANSWERED: '답변 완료',
  TRANSFERRED: '부서 검토 중',
  CLOSED: '종결',
};

export const CHANNEL_LABEL: Record<TicketChannel, string> = {
  EMAIL: '이메일',
  PARTNER_PORTAL: '파트너 포털',
  WEB_FORM: '웹 폼',
  PHONE: '전화',
  CHAT: '채팅',
};

export const REQUEST_TYPE_LABEL: Record<RequestSummary['request_type'], string> = {
  FAULT: '제품 불량',
  DOCUMENT: '자료 문서',
};

export const REQUEST_STATUS_LABEL: Record<RequestSummary['status'], string> = {
  DRAFT: '작성 중',
  REVIEWED: '검토 완료',
  SENT: '부서에 보냄',
  ACKNOWLEDGED: '부서 확인',
  ADOPTED: '채택',
  INFO_REQUESTED: '부서가 정보 요청',
  REJECTED: '반려',
  ACTION_TAKEN: '조치함',
  FOLLOW_UP: '재발 점검 중',
  COMPLETED: '완료',
};

// 2026-10-28 09:02 형식(브라우저 시간대).
export function formatStamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '-';
  const two = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

export const dayOf = (iso: string) => formatStamp(iso).slice(0, 10);
