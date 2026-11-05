import type { components } from './generated/tft';
import { request, sessionCsrfToken } from './client';
export const CONTEXT_CATEGORIES = { EQUIPMENT:'설비·장비', TASK:'수행과업', DELIVERABLE:'제출물', INTERFACE:'인터페이스', QUALITY:'품질·시험', SCHEDULE:'일정', QUALIFICATION:'자격', COMMERCIAL:'상업조건', UNCLASSIFIED:'미분류' } as const;
type Schemas=components['schemas'];
export type ContextCategory = Schemas['ContextAssignment']['primary_category'];
export type ContextGroup = Schemas['ContextGroup'] & {diagram_source_ids:string[]};
export type ContextAssignment = Required<Schemas['ContextAssignment']>;
export type ContextResult = Omit<Schemas['ContextResult'],'groups'|'assignments'|'issues'> & {groups:ContextGroup[];assignments:ContextAssignment[];issues:Schemas['ContextIssue'][]};
export type ContextSource = Schemas['SourceReference'];
export type ContextRequirement = Schemas['ContextRequirement'];
export type ContextEnvelope = Omit<Schemas['ContextResponse'],'result'> & {result:ContextResult|null};
export type ContextPatch = Schemas['ContextCorrection'];
// The contract permits omitted default arrays. Normalize only these defaults;
// no requirement, judgement, group or evidence is invented by the client.
function normalized(response:{data:Schemas['ContextResponse']}):{data:ContextEnvelope} {
  const result=response.data.result;
  return {data:{...response.data,result:result?{...result,issues:result.issues??[],groups:result.groups.map(g=>({...g,diagram_source_ids:g.diagram_source_ids??[]})),assignments:result.assignments.map(a=>({...a,additional_categories:a.additional_categories??[],group_ids:a.group_ids??[],relations:a.relations??[],evidence_requirement_ids:a.evidence_requirement_ids??[]}))}:null}};
}
const path=(id:string)=>`/api/v1/tft/projects/${encodeURIComponent(id)}/context`;
const mutation=(method:string,body:unknown,key:string)=>({method,headers:{'Idempotency-Key':key,...(sessionCsrfToken()?{'X-CSRF-Token':sessionCsrfToken()!}:{})},body:JSON.stringify(body)});
export const getContext=(id:string,signal?:AbortSignal)=>request<{data:Schemas['ContextResponse']}>(path(id),{signal}).then(normalized);
export const processContext=(id:string,key:string)=>request<{data:Schemas['JobAccepted']}>(`${path(id)}/process`,mutation('POST',{},key));
export const updateContext=(id:string,body:ContextPatch,key:string)=>request<{data:Schemas['ContextResponse']}>(path(id),mutation('PATCH',body,key)).then(normalized);
