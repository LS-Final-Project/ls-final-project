// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import TicketCreateDialog from './TicketCreateDialog';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
const NEW_ID = '55555555-5555-4555-8555-555555555555';
type Post = { url: string; body: Record<string, unknown>; token: string | null };

function server(reply: () => Response = () => jsonResponse(201, { data: { id: NEW_ID } }), roles = ['cs_agent']) {
  const posts: Post[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', roles } }), { status: 200, headers: { 'X-CSRF-Token': 'tok' } });
    posts.push({ url: path, body: JSON.parse(String(init?.body ?? '{}')), token: new Headers(init?.headers).get('X-CSRF-Token') });
    return reply();
  }));
  return posts;
}
function view(live = true) {
  setApiMode(live ? 'live' : 'mock');
  const onClose = vi.fn();
  const onCreated = vi.fn();
  renderAt('/cs/tickets', '/cs/tickets', <><TicketCreateDialog products={['TRF-OIL-1000', 'ACB-AS-2000']} onClose={onClose} onCreated={onCreated} />
    <Routes><Route path="*" element={null} /></Routes></>);
  return { onClose, onCreated };
}
const fill = (over: Record<string, string> = {}) => {
  const values: Record<string, string> = { 고객: 'CUST-0374', 제품: 'TRF-OIL-1000', 본문: '전화로 누유 신고가 들어왔습니다.', ...over };
  fireEvent.change(screen.getByLabelText('고객 ID'), { target: { value: values.고객 } });
  fireEvent.change(screen.getByLabelText('제품 코드'), { target: { value: values.제품 } });
  fireEvent.change(screen.getByLabelText('본문'), { target: { value: values.본문 } });
};
const submit = () => fireEvent.submit(screen.getByRole('dialog').querySelector('form')!);

test('입력한 값만 보내고 출처·판본·ID는 보내지 않으며, 등록하면 닫고 목록을 다시 읽는다', async () => {
  const posts = server();
  const { onClose, onCreated } = view();
  fill();
  fireEvent.change(screen.getByLabelText('접수 경로'), { target: { value: 'CHAT' } });
  fireEvent.change(screen.getByLabelText('제목'), { target: { value: '  누유 신고  ' } });
  fireEvent.change(screen.getByLabelText('보고된 펌웨어'), { target: { value: '1.12' } });
  submit();
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(posts).toHaveLength(1);
  expect(posts[0].url).toBe('/api/v1/cs/tickets');
  expect(posts[0].body).toEqual({ channel: 'CHAT', body_language: 'ko', customer_external_id: 'CUST-0374', product_code: 'TRF-OIL-1000', body: '전화로 누유 신고가 들어왔습니다.', subject: '누유 신고', fw_version_reported: '1.12', error_code_reported: null });
  expect(posts[0].token).toBe('tok');
  expect(onCreated).toHaveBeenCalled();
});

test('고객 ID 형식이 틀리면 보내지 않고 안내한다', async () => {
  const posts = server();
  view();
  fill({ 고객: '고객 1' });
  submit();
  expect((await screen.findByRole('alert')).textContent).toContain('고객 ID는 영문·숫자');
  expect(posts).toHaveLength(0);
});

test('서버 거부(4xx)는 이유를 보이고 다시 시도할 수 있다', async () => {
  const posts = server(() => jsonResponse(422, errorBody('VALIDATION', '알 수 없는 제품 코드입니다.')));
  view();
  fill();
  submit();
  expect((await screen.findByRole('alert')).textContent).toContain('알 수 없는 제품 코드입니다.');
  expect((screen.getByRole('button', { name: '등록' }) as HTMLButtonElement).disabled).toBe(false);
  expect(posts).toHaveLength(1);
});

test('결과를 알 수 없으면(5xx) 같은 문의를 다시 등록하지 않고 목록 확인을 안내한다', async () => {
  const posts = server(() => jsonResponse(503, errorBody('UNAVAILABLE', '일시 장애')));
  const { onCreated, onClose } = view();
  fill();
  submit();
  expect((await screen.findByRole('alert')).textContent).toContain('다시 등록하지 말고 목록을 조회해');
  expect((screen.getByRole('button', { name: '등록' }) as HTMLButtonElement).disabled).toBe(true);
  submit();
  expect(posts).toHaveLength(1);
  expect(onCreated).toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});

test('C/S 담당자가 아니거나 모의 응답 모드면 요청을 보내지 않는다', async () => {
  const posts = server(undefined, ['sales']);
  view();
  fill();
  submit();
  expect((await screen.findByRole('alert')).textContent).toContain('C/S 담당자 세션이 필요합니다');
  cleanup();
  view(false);
  fill();
  submit();
  expect((await screen.findByRole('alert')).textContent).toContain('모의 응답 모드');
  expect(posts).toHaveLength(0);
});
