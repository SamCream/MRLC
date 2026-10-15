const assert=require('node:assert/strict');
const path=require('node:path');
const output=process.argv[2];
const {ApiClient}=require(path.join(output,'api/client.js'));
const {createServices}=require(path.join(output,'api/services.js'));
const {httpOrganizationRepository}=require(path.join(output,'data/httpOrganizationRepository.js'));
const calls=[];
const fakeFetch=async(url,init={})=>{
  calls.push({url,method:init.method||'GET',body:init.body&&typeof init.body==='string'?JSON.parse(init.body):null,authorization:new Headers(init.headers).get('Authorization')});
  return new Response(JSON.stringify({items:[],id:'mock-id',name:'Demo',status:'idle'}),{status:200,headers:{'Content-Type':'application/json'}});
};
const api=createServices(new ApiClient({baseUrl:'http://local.test/',getAccessToken:()=> 'demo-token',fetcher:fakeFetch}));
const repo=httpOrganizationRepository(api);
const run=async()=>{
  await api.auth.me();
  await api.auth.tokenStatus();
  await api.auth.rotateToken('custom_login_token_12345678901234567890');
  await api.auth.revokeToken();
  await api.infra.status();
  await api.infra.overview('project/id');
  await api.infra.events('project/id');
  await api.infra.replay('project/id');
  await api.infra.replayCompare('project/id',1,2);
  await api.infra.scheduler('project/id');
  await api.infra.memory('project/id','shared');
  await api.infra.context('project/id');
  await api.infra.bank('project/id');
  await api.infra.frontier('project/id');
  await api.infra.frontierConsult('project/id',{parentTaskId:'task/id',ownerSessionId:'session/id',capability:'review proof',budget:100,operationId:'op-1'});
  await api.infra.provenance('project/id');
  await api.infra.invocations('project/id');
  await api.infra.consistency('project/id');
  await api.infra.operations('project/id');
  await api.infra.backups();
  await api.infra.createBackup();
  await api.infra.verifyBackup('backup/id');
  await repo.listProjects();
  await repo.createProject({name:'Demo',description:'Desc'});
  await repo.updateProject('project/id',{name:'Updated'});
  await repo.archiveProject('project/id','finished');
  await repo.listRoles();
  await repo.createRole({name:'Reviewer',purpose:'Review',capabilities:['Review'],allowedPermissions:['tasks.review']});
  await repo.updateRole('role-id',{purpose:'New'});
  await repo.transitionRole('role-id','park');
  await repo.listAgents('project/id');
  await repo.assignAgent({projectId:'project/id',roleTemplateId:'role-id',name:'R'});
  await repo.parkAgent('agent-id');
  await repo.resumeAgent('agent-id');
  assert.deepEqual(calls.map(c=>[c.method,c.url.replace('http://local.test','')]),[
    ['GET','/api/v1/auth/me'],
    ['GET','/api/v1/auth/me/token'],
    ['POST','/api/v1/auth/me/token'],
    ['DELETE','/api/v1/auth/me/token'],
    ['GET','/api/v1/infra/status'],
    ['GET','/api/v1/projects/project%2Fid/infra/overview'],
    ['GET','/api/v1/projects/project%2Fid/infra/events'],
    ['GET','/api/v1/projects/project%2Fid/infra/replay'],
    ['GET','/api/v1/projects/project%2Fid/infra/replay/compare?fromSequence=1&toSequence=2'],
    ['GET','/api/v1/projects/project%2Fid/infra/scheduler'],
    ['GET','/api/v1/projects/project%2Fid/infra/memory?roleId=shared'],
    ['GET','/api/v1/projects/project%2Fid/infra/context'],
    ['GET','/api/v1/projects/project%2Fid/infra/bank'],
    ['GET','/api/v1/projects/project%2Fid/infra/frontier'],
    ['POST','/api/v1/projects/project%2Fid/infra/frontier/consult'],
    ['GET','/api/v1/projects/project%2Fid/infra/provenance'],
    ['GET','/api/v1/projects/project%2Fid/infra/invocations'],
    ['GET','/api/v1/projects/project%2Fid/infra/consistency'],
    ['GET','/api/v1/projects/project%2Fid/infra/operations'],
    ['GET','/api/v1/infra/backups'],
    ['POST','/api/v1/infra/backups'],
    ['POST','/api/v1/infra/backups/backup%2Fid/verify'],
    ['GET','/api/v1/projects'],
    ['POST','/api/v1/projects'],
    ['PATCH','/api/v1/projects/project%2Fid'],
    ['POST','/api/v1/projects/project%2Fid/archive'],
    ['GET','/api/v1/roles'],
    ['POST','/api/v1/roles'],
    ['PATCH','/api/v1/roles/role-id'],
    ['POST','/api/v1/roles/role-id/park'],
    ['GET','/api/v1/projects/project%2Fid/agents'],
    ['POST','/api/v1/projects/project%2Fid/agents'],
    ['POST','/api/v1/agents/agent-id/park'],
    ['POST','/api/v1/agents/agent-id/resume'],
  ]);
  assert.deepEqual(calls[2].body,{token:'custom_login_token_12345678901234567890'});
  assert.deepEqual(calls[25].body,{reason:'finished'});
  assert.deepEqual(calls[31].body,{projectId:'project/id',roleTemplateId:'role-id',name:'R'});
  assert.deepEqual(calls[14].body,{parentTaskId:'task/id',ownerSessionId:'session/id',capability:'review proof',budget:100,operationId:'op-1'});
  assert.equal(calls.every(c=>c.authorization==='Bearer demo-token'),true);
  console.log('PASS: 34 HTTP adapter routes including Infra Console, auth-token management, ID encoding, request bodies and bearer headers');
};
run().catch(e=>{console.error(e);process.exitCode=1;});
