const assert=require('node:assert/strict');const path=require('node:path');const base=process.argv[2];
const {ApiClient}=require(path.join(base,'api/client.js'));
const {createServices}=require(path.join(base,'api/services.js'));
const {httpSystemRepository,httpSettingsRepository}=require(path.join(base,'data/httpSystemRepository.js'));
const calls=[];
const fetcher=async(url,opts={})=>{
  calls.push({url:String(url).replace('https://example.invalid',''),method:opts.method||'GET',body:opts.body,auth:new Headers(opts.headers).get('authorization')});
  return new Response(JSON.stringify({items:[],nodes:[],services:[],revision:2}),{status:200,headers:{'Content-Type':'application/json'}});
};
(async()=>{
  const services=createServices(new ApiClient({baseUrl:'https://example.invalid/',fetcher,getAccessToken:()=> 'bearer'}));
  const system=httpSystemRepository(services);const settings=httpSettingsRepository(services,{
    getAppearance:async()=>({theme:'strata-dark',motion:'highlights',density:'compact',stageAnimation:'play-once'}),
    saveAppearance:async x=>x,resetAppearance:async()=>({theme:'strata-dark',motion:'highlights',density:'compact',stageAnimation:'play-once'}),
    subscribe:()=>()=>{},getRuntimeSettings:async()=>{throw new Error('unexpected');},updateRuntimeSettings:async()=>{throw new Error('unexpected');},resetRuntimeSettings:async()=>{throw new Error('unexpected');}
  });
  await system.getHealth();await system.runHealthCheck();await system.getTopology();
  await system.listNodes('local');await system.listAccelerators();
  await system.listLogs({projectId:'p/a',level:'warn',q:'SQL & roles',cursor:'a==',limit:20});
  await system.listLogViews();await system.saveLogView('My view',{projectId:'p/a'});await system.deleteLogView('view x');
  await settings.getRuntimeSettings('p/a');
  await settings.updateRuntimeSettings('p/a',{modelId:'llama/32b',contextPolicy:'task',sessionPolicy:'resumable',frontierEscalation:'ask'},2);
  await settings.resetRuntimeSettings('p/a',3);
  await services.system.overview('p/a');
  await services.system.exportLogs({filter:{projectId:'p/a'},format:'csv'});
  assert.deepEqual(calls.map(x=>[x.method,x.url]),[
    ['GET','/api/v1/system/health'],['POST','/api/v1/system/health-checks'],['GET','/api/v1/system/topology'],['GET','/api/v1/system/nodes?scope=local'],['GET','/api/v1/system/accelerators'],
    ['GET','/api/v1/system/logs?projectId=p%2Fa&level=warn&q=SQL+%26+roles&cursor=a%3D%3D&limit=20'],
    ['GET','/api/v1/users/me/log-views'],['POST','/api/v1/users/me/log-views'],['DELETE','/api/v1/users/me/log-views/view%20x'],
    ['GET','/api/v1/projects/p%2Fa/settings'],['PATCH','/api/v1/projects/p%2Fa/settings'],['POST','/api/v1/projects/p%2Fa/settings/reset'],
    ['GET','/api/v1/projects/p%2Fa/overview'],['POST','/api/v1/system/logs/export']]);
  assert.equal(calls.every(x=>x.auth==='Bearer bearer'),true);
  assert.deepEqual(JSON.parse(calls[7].body),{name:'My view',filter:{projectId:'p/a'}});
  assert.deepEqual(JSON.parse(calls[10].body),{modelId:'llama/32b',contextPolicy:'task',sessionPolicy:'resumable',frontierEscalation:'ask',expectedRevision:2});
  assert.deepEqual(JSON.parse(calls[11].body),{expectedRevision:3});
  assert.deepEqual(JSON.parse(calls[13].body),{filter:{projectId:'p/a'},format:'csv'});
  console.log('PASS: 14 Phase 5 HTTP candidate routes with URL encoding, token forwarding, filters and revision payload');
})().catch(e=>{console.error(e);process.exitCode=1;});
