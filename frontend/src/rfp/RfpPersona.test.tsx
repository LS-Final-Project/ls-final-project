// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { StrictMode } from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const fake = vi.hoisted(() => ({
  inputs: { listening:{value:false}, thinking:{value:false}, speaking:{value:false}, asleep:{value:false} },
  play:vi.fn(),pause:vi.fn(),drawFrame:vi.fn(),setRgb:vi.fn(),params:null as any,
  colorValue:0xffffffff as number|null,created:0,disposed:0,active:0,maxActive:0,autoLoad:true,primary:'',fallback:'',threads:undefined as unknown,
}));
vi.mock('@rive-app/react-webgl2', async () => {
  const React = await import('react');
  const rive = {play:fake.play,pause:fake.pause,drawFrame:fake.drawFrame};
  const Canvas = (props:any) => <div className={props.className}><canvas aria-hidden="true" /></div>;
  return {
    Alignment:{Center:'center'},Fit:{Contain:'contain'},Layout:class { constructor(_options:unknown) {} },
    RuntimeLoader:{setWasmUrl:(url:string)=>fake.primary=url,setWasmFallbackUrl:(url:string)=>fake.fallback=url,setWasmThreadsUrl:(value:unknown)=>fake.threads=value},
    useRive:(params:any)=>{
      fake.params=params;
      React.useEffect(()=>{
        if(!params)return;
        fake.created++;fake.active++;fake.maxActive=Math.max(fake.maxActive,fake.active);
        if(fake.autoLoad)params.onRiveReady();
        return()=>{fake.disposed++;fake.active--;};
      },[Boolean(params)]);
      return {rive:params?rive:null,RiveComponent:Canvas};
    },
    useStateMachineInput:(value:unknown,_machine:string,name:keyof typeof fake.inputs)=>value?fake.inputs[name]:null,
    useViewModel:()=>null,useViewModelInstance:()=>null,useViewModelInstanceColor:()=>({value:fake.colorValue,setRgb:fake.setRgb}),
  };
});
import { RfpPersona } from './RfpPersona';
let reduced=false;
let listeners:Set<()=>void>;
beforeEach(()=>{
  vi.useFakeTimers();listeners=new Set();reduced=false;
  fake.colorValue=0xffffffff;fake.created=0;fake.disposed=0;fake.active=0;fake.maxActive=0;fake.autoLoad=true;
  fake.play.mockClear();fake.pause.mockClear();fake.drawFrame.mockClear();fake.setRgb.mockClear();
  for(const input of Object.values(fake.inputs))input.value=false;
  vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>setTimeout(()=>callback(performance.now()),16));
  vi.stubGlobal('cancelAnimationFrame',(id:number)=>clearTimeout(id));
  vi.stubGlobal('matchMedia',()=>({get matches(){return reduced;},addEventListener:(_name:string,callback:()=>void)=>listeners.add(callback),removeEventListener:(_name:string,callback:()=>void)=>listeners.delete(callback)}));
});
afterEach(()=>{cleanup();vi.useRealTimers();vi.unstubAllGlobals();});
const frame=()=>act(()=>vi.advanceTimersByTime(20));

it('공식 Halo 하나를 StrictMode 후 초기화하고 상태 변경은 같은 인스턴스 입력만 바꾼다',()=>{
  const view=render(<StrictMode><RfpPersona state="idle" paused={false} label="AI 대기 중"/></StrictMode>);
  expect(fake.created).toBe(0);frame();
  expect(fake.created).toBe(1);expect(fake.maxActive).toBe(1);
  expect(fake.params.src).toBe('https://ejiidnob33g9ap1r.public.blob.vercel-storage.com/halo-2.0.riv');
  expect(fake.params.stateMachines).toBe('default');
  expect(fake.params.enableRiveAssetCDN).toBe(false);expect(fake.params.automaticallyHandleEvents).toBe(false);
  expect(fake.setRgb).toHaveBeenCalledWith(0,0,0);
  view.rerender(<StrictMode><RfpPersona state="thinking" paused={false} label="AI 분석 중"/></StrictMode>);
  expect(fake.inputs.thinking.value).toBe(true);expect(fake.inputs.speaking.value).toBe(false);
  expect(screen.getByRole('img',{name:'AI 분석 중'}).getAttribute('data-state')).toBe('thinking');
  view.rerender(<StrictMode><RfpPersona state="speaking" paused={false} label="AI 초안 작성 중"/></StrictMode>);
  expect(fake.inputs.thinking.value).toBe(false);expect(fake.inputs.speaking.value).toBe(true);
  expect(fake.created).toBe(1);expect(screen.getByRole('img').querySelectorAll('canvas')).toHaveLength(1);
  view.unmount();expect(fake.active).toBe(0);expect(fake.disposed).toBe(1);
});

it('완료·중단에서는 재생을 멈추며 업무 라벨을 정적 표시와 별도로 유지한다',()=>{
  const view=render(<RfpPersona state="thinking" paused={false} label="AI 분석 중"/>);frame();
  fake.play.mockClear();
  view.rerender(<RfpPersona state="idle" paused label="분석 완료"/>);
  expect(fake.pause).toHaveBeenCalledWith('default');expect(fake.drawFrame).toHaveBeenCalled();expect(fake.play).not.toHaveBeenCalled();
  expect(screen.getByRole('img',{name:'분석 완료'}).getAttribute('data-paused')).toBe('true');
  expect(fake.inputs.thinking.value).toBe(false);expect(fake.inputs.speaking.value).toBe(false);
});

it('WASM 기본·호환 경로가 자체서빙이고 외부 WASM fallback과 threads를 사용하지 않는다',()=>{
  expect(fake.primary).toBe('/vendor/rive/2.48.0/rive.wasm');
  expect(fake.fallback).toBe('/vendor/rive/2.48.0/rive_fallback.wasm');
  expect(fake.threads).toBe(null);
});

it('동작 줄이기는 WebGL을 만들지 않으며 변경·해제·unmount에서 정리한다',()=>{
  reduced=true;
  const view=render(<RfpPersona state="thinking" paused={false} label="AI 분석 중"/>);frame();
  expect(fake.created).toBe(0);expect(screen.getByRole('img').querySelector('[data-fallback-reason="reduced-motion"]')).toBeTruthy();
  act(()=>{reduced=false;for(const listener of listeners)listener();});frame();
  expect(fake.active).toBe(1);
  act(()=>{reduced=true;for(const listener of listeners)listener();});
  expect(fake.active).toBe(0);view.unmount();expect(listeners.size).toBe(0);
});

it('외부 Halo 실패는 정적 AI로 대체하고 자동 재시도·업무 상태 변경을 하지 않는다',()=>{
  const view=render(<RfpPersona state="speaking" paused={false} label="AI 초안 작성 중"/>);frame();
  act(()=>fake.params.onLoadError({message:'external asset unavailable'}));
  const element=screen.getByRole('img',{name:'AI 초안 작성 중'});
  expect(element.querySelector('[data-fallback-reason="unavailable"]')).toBeTruthy();expect(element.querySelector('canvas')).toBeNull();expect(fake.active).toBe(0);
  view.rerender(<RfpPersona state="thinking" paused={false} label="AI 분석 중"/>);frame();
  expect(fake.created).toBe(1);expect(screen.getByRole('img',{name:'AI 분석 중'})).toBeTruthy();
});

it('리소스 로딩이 끝나지 않아도 표시만 fallback 처리하고 unmount 후 타이머를 남기지 않는다',()=>{
  fake.autoLoad=false;
  const view=render(<RfpPersona state="idle" paused={false} label="AI 대기 중"/>);frame();
  act(()=>vi.advanceTimersByTime(15_000));
  expect(screen.getByRole('img').querySelector('[data-fallback-reason="unavailable"]')).toBeTruthy();expect(fake.active).toBe(0);
  view.unmount();expect(vi.getTimerCount()).toBe(0);
});


it('자체서빙 WASM 두 파일이 잠금된 런타임 원본과 일치한다',()=>{
  for(const filename of ['rive.wasm','rive_fallback.wasm']){
    const supplied=readFileSync(`public/vendor/rive/2.48.0/${filename}`);
    const installed=readFileSync(`node_modules/@rive-app/webgl2/${filename}`);
    expect(supplied.subarray(0,4)).toEqual(Buffer.from([0,97,115,109]));
    expect(supplied.equals(installed)).toBe(true);
  }
});

it('동작 줄이기를 해제한 새 인스턴스도 로딩·실패 제한을 새로 적용한다',()=>{
  render(<RfpPersona state="thinking" paused={false} label="AI 분석 중"/>);frame();
  expect(screen.getByRole('img').querySelector('[data-renderer="rive"]')).toBeTruthy();
  act(()=>{reduced=true;for(const listener of listeners)listener();});
  expect(fake.active).toBe(0);fake.autoLoad=false;
  act(()=>{reduced=false;for(const listener of listeners)listener();});frame();
  expect(fake.active).toBe(1);expect(fake.created).toBe(2);
  expect(screen.getByRole('img').querySelector('[data-renderer="loading"]')).toBeTruthy();
  act(()=>vi.advanceTimersByTime(15_000));
  expect(screen.getByRole('img').querySelector('[data-fallback-reason="unavailable"]')).toBeTruthy();
  expect(fake.active).toBe(0);
});

it('색상 바인딩이 늦게 준비되어도 밝은 화면용 검정색을 적용한다',()=>{
  fake.colorValue=null;
  const view=render(<RfpPersona state="idle" paused={false} label="AI 대기 중"/>);frame();
  expect(fake.setRgb).not.toHaveBeenCalled();
  fake.colorValue=0xffffffff;
  view.rerender(<RfpPersona state="thinking" paused={false} label="AI 분석 중"/>);
  expect(fake.setRgb).toHaveBeenCalledWith(0,0,0);
});

it('완료는 움직이던 마지막 프레임을 정리하고 정돈된 링과 체크로 표시한다',()=>{
  const view=render(<RfpPersona state="thinking" paused={false} label="AI 분석 중"/>);frame();
  expect(fake.active).toBe(1);fake.pause.mockClear();
  view.rerender(<RfpPersona state="idle" paused completed label="분석 완료"/>);
  const persona=screen.getByRole('img',{name:'분석 완료'});
  expect(persona.querySelector('[data-renderer="completed"] circle')).toBeTruthy();
  expect(persona.querySelector('[data-renderer="completed"] path')).toBeTruthy();
  expect(persona.querySelector('canvas')).toBeNull();
  expect(fake.active).toBe(0);expect(fake.pause).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
  view.rerender(<RfpPersona state="idle" paused={false} label="AI 대기 중"/>);frame();
  expect(persona.querySelector('[data-renderer="completed"]')).toBeNull();
  expect(fake.active).toBe(1);expect(fake.created).toBe(2);
});

it('완료표식은 감소 모션 상태에서도 애니메이션이나 외부 리소스 없이 표시된다',()=>{
  reduced=true;
  render(<RfpPersona state="idle" paused completed label="분석 완료"/>);frame();
  expect(screen.getByRole('img',{name:'분석 완료'}).querySelector('[data-renderer="completed"]')).toBeTruthy();
  expect(fake.created).toBe(0);expect(vi.getTimerCount()).toBe(0);
});
