import { afterEach, expect, it, vi } from 'vitest';
import { getContext, processContext, updateContext } from './rfp-context';
import { request, sessionCsrfToken } from './client';
vi.mock('./client',()=>({request:vi.fn(async()=>({data:{result:null}})),sessionCsrfToken:vi.fn(()=> 'csrf')}));
afterEach(()=>vi.clearAllMocks());
it('GET is read-only and passes abort signal to the exact project path',()=>{const signal=new AbortController().signal;getContext('p/id',signal);expect(request).toHaveBeenCalledWith('/api/v1/tft/projects/p%2Fid/context',{signal});});
it('process and correction use CSRF and caller idempotency without replacing requirements',()=>{processContext('p','key');expect(request).toHaveBeenLastCalledWith('/api/v1/tft/projects/p/context/process',expect.objectContaining({method:'POST',headers:{'Idempotency-Key':'key','X-CSRF-Token':'csrf'},body:'{}'}));const body={expected_version:2,expected_source_fingerprint:'a'.repeat(64),reason:'수정',result:{} as never};updateContext('p',body,'edit-key');expect(request).toHaveBeenLastCalledWith('/api/v1/tft/projects/p/context',expect.objectContaining({method:'PATCH',headers:{'Idempotency-Key':'edit-key','X-CSRF-Token':'csrf'},body:JSON.stringify(body)}));expect(sessionCsrfToken).toHaveBeenCalled();});

it('contract optional arrays normalize without creating groups or evidence',async()=>{
 vi.mocked(request).mockResolvedValueOnce({data:{result:{groups:[{entity_id:'g',name:'설비',kind:'EQUIPMENT',evidence_requirement_ids:['r'],needs_review:true}],assignments:[{requirement_id:'r',primary_category:'EQUIPMENT',reason:'근거',needs_review:true}],outcome:'PARTIAL',schema_version:'rfp-context-v1',prompt_version:'rfp-context-v1'}}});
 const result=(await getContext('p')).data.result!;
 expect(result.groups).toHaveLength(1);expect(result.groups[0].diagram_source_ids).toEqual([]);
 expect(result.assignments[0]).toMatchObject({additional_categories:[],group_ids:[],relations:[],evidence_requirement_ids:[]});expect(result.issues).toEqual([]);
});
