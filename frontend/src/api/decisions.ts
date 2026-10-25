import type { components } from './generated/tft';
import { request } from './client';
type Schema = components['schemas'];
type Envelope<T> = {data:T;meta:{request_id:string}};
export type Composition = Schema['CompositionVersion'];
export type Assessment = Schema['TeamAssessment'];
export type Decision = 'confirm' | 'reject' | 'reopen';
export const verification = (id:string) => request<Envelope<Assessment>>(`/api/v1/tft/projects/${encodeURIComponent(id)}/verification`);
export const composition = (id:string,version:number) => request<Envelope<Composition>>(`/api/v1/tft/projects/${encodeURIComponent(id)}/compositions/${version}`);
export const decisions = (id:string,after=0) => request<Envelope<Schema['DecisionHistory'][]>>(`/api/v1/tft/projects/${encodeURIComponent(id)}/decisions?limit=100&after_version=${after}`);
const attempts=new Map<string,string>();
export async function decide(id:string,version:number,action:Decision,compositionVersion:number,reason:string){
  const path=`/api/v1/tft/projects/${encodeURIComponent(id)}/${action}`;
  const body=JSON.stringify(action==='reopen'?{reason}:{composition_version:compositionVersion,reason});
  const slot=`${path}:${version}:${body}`;
  const key=attempts.get(slot)??crypto.randomUUID();attempts.set(slot,key);
  const result=await request<Envelope<Schema['DecisionResult']>>(path,{method:'POST',headers:{'If-Match':`"${version}"`,'Idempotency-Key':key},body});
  attempts.delete(slot);return result;
}
