import { expect, test } from 'vitest';
import { ApiError } from './client';
import { errorText } from './error-text';

test('서버 영어 문구는 업무 코드별 한국어 안내로 바꾸고 원문은 serverMessage에 남긴다', () => {
  const error = new ApiError(409, 'The ticket state does not allow this action.', 'req-1', 'CS_INVALID_TRANSITION');
  expect(error.message).toBe('지금 문의 상태에서는 할 수 없는 동작입니다. 최신 상태를 다시 읽은 뒤 확인하세요.');
  expect(error.serverMessage).toBe('The ticket state does not allow this action.');
  expect(error.code).toBe('CS_INVALID_TRANSITION');
  expect(errorText('TFT_VERSION_CONFLICT', 'The record changed. Reload and retry.')).toMatch(/^그 사이 내용이 바뀌었습니다/);
  expect(errorText('MAIL_RESEND_UNVERIFIED', 'Check Sent Items before resending.')).toMatch(/보낸 편지함/);
});

test('서버가 이미 한국어로 준 구체적인 사유는 덮지 않는다', () => {
  expect(errorText('MAIL_RESEND_NOT_ALLOWED', '유예 시간이 지나지 않았습니다.')).toBe('유예 시간이 지나지 않았습니다.');
});

test('입력 검증 실패는 어느 칸이 문제인지 알 수 있게 서버 원문을 붙인다', () => {
  expect(errorText('VALIDATION_ERROR', 'body.version: field required')).toBe('입력값을 확인하세요. (body.version: field required)');
});

test('표에 없는 업무 코드는 영어 대신 코드를 붙인 일반 안내를 보인다', () => {
  expect(errorText('CS_SOMETHING_NEW', 'Something new happened.')).toBe('요청을 처리하지 못했습니다(CS_SOMETHING_NEW). 최신 상태를 다시 읽은 뒤 확인하세요.');
});

test('업무 밖 코드(평가·작업 운영)와 코드 없는 오류는 서버 문구를 그대로 둔다', () => {
  expect(errorText('EVALUATION_TARGET_DUPLICATE', 'Target refs must be unique.')).toBe('Target refs must be unique.');
  expect(errorText(undefined, 'HTTP 502')).toBe('HTTP 502');
});
