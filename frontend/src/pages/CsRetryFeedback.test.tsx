import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import ImprovementVersions from '../components/ImprovementVersions';
import TicketUnits from '../components/TicketUnits';
import CsStats from './CsStats';
import CsImprovements from './CsImprovements';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';
const cases=[
 {name:'판본',element:<ImprovementVersions requestId="test"/>,path:'/',pattern:'/',open:'판본 이력 조회',refresh:'판본 이력 새로고침',loaded:'저장된 판본이 없습니다.',body:()=>({data:{items:[]}})},
 {name:'장비',element:<TicketUnits ticketId="test"/>,path:'/',pattern:'/',open:'장비 정보 조회',refresh:'장비 정보 새로고침',loaded:'확정 장비가 없어 이전 문의를 표시하지 않습니다.',body:(url:string)=>({data:url.endsWith('/units')?{items:[]}:{status:'NO_CONFIRMED_UNIT',unit:null,items:[]}})},
 {name:'통계',element:<CsStats/>,path:'/cs/stats',pattern:'/cs/stats',open:null,refresh:'최신 현황 조회',loaded:/처리 현황 \(/,body:()=>examples.find(x=>x.path==='/api/v1/cs/stats/overview')!.body},
 {name:'개선 목록',element:<CsImprovements/>,path:'/cs/improvements',pattern:'/cs/improvements',open:null,refresh:'최신 요청 조회',loaded:'조회 가능한 개선요청이 없습니다.',body:()=>({data:{items:[]}})},
 {name:'개선 상세',element:<CsImprovements/>,path:'/cs/improvements/test',pattern:'/cs/improvements/:requestId',open:null,refresh:'최신 요청 조회',loaded:'근거 문의',body:()=>examples.find(x=>x.path==='/api/v1/cs/improvement-requests/dddddddd-dddd-4ddd-8ddd-ddddddddddd1')!.body},
];
afterEach(()=>{cleanup();vi.unstubAllGlobals();setApiMode('mock');});
test.each(cases)('$name: 지연 재시도 중 이전 alert를 숨기고 새 실패를 표시한다',async c=>{
 let phase:'success'|'failure'|'pending'='success';let release!:()=>void;
 const pending=new Promise<void>(resolve=>{release=resolve;});
 vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL)=>{
  if(phase==='success')return jsonResponse(200,c.body(String(url)));
  if(phase==='failure')return jsonResponse(503,errorBody('OLD','이전 조회 실패'));
  await pending;return jsonResponse(503,errorBody('NEW','이번 재시도 실패'));
 }));
 setApiMode('live');renderAt(c.path,c.pattern,c.element);
 if(c.open)fireEvent.click(screen.getByRole('button',{name:c.open}));
 await screen.findByText(c.loaded);phase='failure';fireEvent.click(screen.getByRole('button',{name:c.refresh}));
 await screen.findByRole('alert');phase='pending';fireEvent.click(screen.getByRole('button',{name:'다시 시도'}));
 await screen.findByRole('status');expect(screen.queryByRole('alert')).toBeNull();
 expect((screen.getByRole('button',{name:c.refresh}) as HTMLButtonElement).disabled).toBe(true);
 release();await screen.findByText('이번 재시도 실패');expect(screen.queryByRole('status')).toBeNull();expect(screen.getByRole('alert')).toBeTruthy();
});
