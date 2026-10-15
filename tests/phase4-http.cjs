const assert=require('node:assert/strict');
const path=require('node:path');
const build=process.argv[2];
const {ApiClient,ApiError}=require(path.join(build,'api/client.js'));
const {createServices}=require(path.join(build,'api/services.js'));
const {httpArtifactRepository,httpApprovalRepository,httpComputeRepository}=require(path.join(build,'data/httpPhase4Repositories.js'));
const calls=[];
const fetcher=async(url,opts={})=>{
  calls.push({url,method:opts.method||'GET',body:opts.body,auth:new Headers(opts.headers).get('Authorization'),headers:new Headers(opts.headers)});
  if(String(url).includes('/content'))return new Response(new Blob(['server bytes']),{status:200,headers:{'Content-Type':'application/octet-stream'}});
  return new Response(JSON.stringify({items:[],id:'id',status:'pending'}),{status:200,headers:{'Content-Type':'application/json'}});
};
(async()=>{
const api=createServices(new ApiClient({baseUrl:'https://example.invalid/',getAccessToken:()=> 'secret',fetcher}));
const a=httpArtifactRepository(api),p=httpApprovalRepository(api),c=httpComputeRepository(api);
await a.listArtifacts('project/one');await a.getArtifact('file one');
await a.uploadArtifact('project/one',new File(['contents'],'proof.tex',{type:'text/plain'}),'task one');
await a.listVersions('file one');await a.listEvidence('file one');
const content=await a.getContent('file one','version one');assert.equal(await content.text(),'server bytes');
await p.listApprovals('project/one');await p.getApproval('app id');await p.listDecisions('app id');
await p.decide('app id',{decision:'reduce_scope',reason:'Only one GPU',expectedRevision:7,scopeOverride:{maxGpuSlots:1}});
await c.listSlots();await c.pauseSlot('slot one');await c.resumeSlot('slot one');await c.releaseSlot('slot one');await c.listQueue();await c.listSessions();await c.getMetrics();await c.getBudget();
const expected=[
 ['GET','/api/v1/projects/project%2Fone/artifacts'],
 ['GET','/api/v1/artifacts/file%20one'],
 ['POST','/api/v1/projects/project%2Fone/artifacts'],
 ['GET','/api/v1/artifacts/file%20one/versions'],
 ['GET','/api/v1/artifacts/file%20one/evidence'],
 ['GET','/api/v1/artifacts/file%20one/content?versionId=version+one'],
 ['GET','/api/v1/approvals?projectId=project%2Fone'],
 ['GET','/api/v1/approvals/app%20id'],
 ['GET','/api/v1/approvals/app%20id/decisions'],
 ['POST','/api/v1/approvals/app%20id/decisions'],
 ['GET','/api/v1/runtime/slots'],
 ['POST','/api/v1/runtime/slots/slot%20one/pause'],
 ['POST','/api/v1/runtime/slots/slot%20one/resume'],
 ['POST','/api/v1/runtime/slots/slot%20one/release'],
 ['GET','/api/v1/runtime/queue'],
 ['GET','/api/v1/runtime/sessions'],
 ['GET','/api/v1/runtime/metrics'],
 ['GET','/api/v1/runtime/budget'],
];
assert.deepEqual(calls.map(x=>[x.method,x.url.replace('https://example.invalid','')]),expected);
assert.equal(calls.every(x=>x.auth==='Bearer secret'),true);
const upload=calls[2].body;assert.equal(upload instanceof FormData,true);
assert.equal(await upload.get('file').text(),'contents');assert.equal(upload.get('taskId'),'task one');
assert.equal(calls[2].headers.has('content-type'),false);
assert.deepEqual(JSON.parse(calls[9].body),{decision:'reduce_scope',reason:'Only one GPU',expectedRevision:7,scopeOverride:{maxGpuSlots:1}});
assert.equal(calls[5].headers.get('authorization'),'Bearer secret');
console.log('PASS: 18 Phase 4 HTTP routes + multipart file bytes, decision CAS, query encoding and authorization');
})().catch(err=>{console.error(err);process.exitCode=1;});
