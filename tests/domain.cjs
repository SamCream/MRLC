/* Runs against generated JS without npm dependencies; tests the actual mock repositories. */
const assert = require('node:assert/strict');
const path = require('node:path');
const root = process.argv[2];
const storage = new Map();
global.localStorage = {
  getItem: key => storage.has(key) ? storage.get(key) : null,
  setItem: (key,val) => storage.set(key,String(val)),
  removeItem: key => storage.delete(key),
};
const {LocalOrganizationRepository} = require(path.join(root,'organizationRepository.js'));
const {LocalWorkspaceRepository} = require(path.join(root,'workspaceRepository.js'));
const repo = new LocalOrganizationRepository();
const workspace = new LocalWorkspaceRepository(repo);
const run = async () => {
  let changes = 0;
  const unsub = repo.subscribe(()=>changes++);
  assert.equal((await repo.listProjects()).items.length,3);
  const fresh = await repo.createProject({name:'  New Research  ',description:'sample'});
  assert.equal(fresh.name,'New Research');
  assert.equal((await repo.listProjects()).items.length,4);
  assert.equal((await workspace.listTasks(fresh.id)).items.length,0,'tasks scoped to project');
  assert.equal((await workspace.listChannels(fresh.id)).items.length,0,'channels scoped to project');
  assert.equal((await repo.listAgents(fresh.id)).items.length,0,'agents scoped to project');
  const changed = await repo.updateProject(fresh.id,{description:'renamed goal'});
  assert.equal(changed.description,'renamed goal');
  const role = await repo.createRole({name:'Validation Bot',purpose:'proof checks',capabilities:['Verify'],allowedPermissions:['artifacts.read'],lifecycle:'active'});
  assert.equal(role.version,'v1');
  const v2=await repo.updateRole(role.id,{name:'Validation Specialist'});
  assert.equal(v2.version,'v2');
  const agent=await repo.assignAgent({projectId:fresh.id,roleTemplateId:role.id});
  assert.equal(agent.status,'idle');
  assert.equal((await repo.listAgents(fresh.id)).items.length,1);
  await repo.parkAgent(agent.id);
  assert.equal((await repo.listAgents(fresh.id)).items[0].status,'parked');
  await repo.resumeAgent(agent.id);
  assert.equal((await repo.listAgents(fresh.id)).items[0].status,'idle');
  await assert.rejects(repo.transitionRole(role.id,'archive'),/active agents/);
  await repo.transitionRole(role.id,'park');
  await assert.rejects(repo.assignAgent({projectId:fresh.id,roleTemplateId:role.id}),/active/);
  await assert.rejects(repo.resumeAgent(agent.id),/inactive/);
  await repo.transitionRole(role.id,'activate');
  const channel=await workspace.createChannel(fresh.id,{name:'Lab',purpose:'Discuss',contextPolicy:'relevant'});
  assert.equal((await workspace.listChannels('project-1')).items.some(c=>c.id===channel.id),false);
  const msg=await workspace.sendMessage(channel.id,'check theorem','single');
  assert.equal((await workspace.listMessages(channel.id)).items[0].id,msg.id);
  const task=await workspace.createTask({projectId:fresh.id,title:'Proof',assigneeRoleId:role.id});
  assert.equal((await workspace.listTasks(fresh.id)).items[0].id,task.id);
  await assert.rejects(workspace.createTask({projectId:fresh.id,title:'Invalid',dependencyIds:['task-1']}),/same project/);
  assert.equal((await workspace.listTasks('project-1')).items.some(t=>t.id===task.id),false);
  assert.equal((await workspace.reviewTask(task.id)).status,'review');
  assert.equal((await workspace.activity(task.id)).items.length,1);
  await repo.archiveProject(fresh.id,'done');
  assert.equal((await repo.listProjects()).items.find(p=>p.id===fresh.id).status,'archived');
  assert.equal((await repo.listAgents(fresh.id)).items[0].status,'parked');
  await assert.rejects(repo.updateProject(fresh.id,{name:'Not editable'}),/read-only/);
  await assert.rejects(repo.resumeAgent(agent.id),/archived/);
  await assert.rejects(workspace.createChannel(fresh.id,{name:'forbidden',purpose:'',contextPolicy:'relevant'}),/archived/);
  await assert.rejects(workspace.createTask({projectId:fresh.id,title:'forbidden'}),/archived/);
  await assert.rejects(workspace.sendMessage(channel.id,'forbidden','single'),/archived/);
  await repo.transitionRole(role.id,'archive');
  assert.equal((await repo.listRoles()).items.find(r=>r.id===role.id).lifecycle,'archived');
  assert.ok(changes>=9,'repository subscribers notified');
  unsub();
  const restored = new LocalOrganizationRepository();
  assert.equal((await restored.listProjects()).items.find(p=>p.id===fresh.id).status,'archived','localStorage persistence');
  console.log('PASS: 25 organization/workspace contract and data-isolation assertions');
};
run().catch(err=>{console.error(err);process.exitCode=1;});
