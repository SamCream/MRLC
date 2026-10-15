import type {Accelerator, AuditLog, InfrastructureNode, LogFilter, LogSavedView, Page, SystemHealth, SystemTopology} from '../domain/models';
import {localWorkspaceRepository, type WorkspaceRepository} from './workspaceRepository';
import {localOrganizationRepository} from './organizationRepository';

/** Original: index.html L145-155, L244, L287-288, L317-321, L326-327. */
export interface SystemRepository {
  getHealth():Promise<SystemHealth|null>;
  runHealthCheck():Promise<SystemHealth|null>;
  getTopology():Promise<SystemTopology|null>;
  listNodes(scope?:'local'|'remote'):Promise<Page<InfrastructureNode>>;
  listAccelerators():Promise<Page<Accelerator>>;
  listLogs(filter?:LogFilter):Promise<Page<AuditLog>>;
  listLogViews():Promise<Page<LogSavedView>>;
  saveLogView(name:string,filter:Omit<LogFilter,'cursor'|'limit'>):Promise<LogSavedView>;
  deleteLogView(id:string):Promise<void>;
  subscribe(listener:()=>void):()=>void;
}
const maxPage=200;
const viewsKey='mrlc.logViews.demo.v5';
function loadViews():LogSavedView[]{try{const raw=localStorage.getItem(viewsKey);if(raw){const x=JSON.parse(raw) as unknown;if(Array.isArray(x))return x.filter(v=>v&&typeof v.id==='string'&&typeof v.name==='string'&&typeof v.filter==='object');}}catch{/* memory fallback */}return [];}

const decodeCursor=(value:string|undefined)=>{
  if(!value)return 0;
  if(!/^\d+$/.test(value))throw new Error('Invalid cursor');
  const number=Number(value);
  if(!Number.isSafeInteger(number))throw new Error('Invalid cursor');
  return number;
};
export class LocalSystemRepository implements SystemRepository {
  constructor(private readonly workspace:WorkspaceRepository=localWorkspaceRepository){}
  private readonly views=loadViews();
  private readonly listeners=new Set<()=>void>();
  private notify(){try{localStorage.setItem(viewsKey,JSON.stringify(this.views));}catch{/* memory */}this.listeners.forEach(f=>f());}
  subscribe=(listener:()=>void)=>{const unsub=this.workspace.subscribe(listener);this.listeners.add(listener);return()=>{unsub();this.listeners.delete(listener);};};
  listLogViews=async()=>({items:this.views.map(v=>({...v,filter:{...v.filter}}))});
  saveLogView=async(name:string,filter:Omit<LogFilter,'cursor'|'limit'>)=>{if(!name.trim()||name.length>80)throw new Error('View name must contain 1–80 characters');const view:LogSavedView={id:crypto.randomUUID(),name:name.trim(),filter:{...filter}};this.views.push(view);this.notify();return view;};
  deleteLogView=async(id:string)=>{const index=this.views.findIndex(v=>v.id===id);if(index<0)throw new Error('View not found');this.views.splice(index,1);this.notify();};
  getHealth=async():Promise<SystemHealth|null>=>null;
  runHealthCheck=async():Promise<SystemHealth|null>=>null; // Cannot claim checks without a connected runtime.
  getTopology=async():Promise<SystemTopology|null>=>null;
  listNodes=async(_scope?:'local'|'remote'):Promise<Page<InfrastructureNode>>=>({items:[]});
  listAccelerators=async():Promise<Page<Accelerator>>=>({items:[]});
  listLogs=async(filter:LogFilter={}):Promise<Page<AuditLog>>=>{
    // Local application events only: never claim this is a complete backend audit stream.
    // The workspace interface currently exposes task-scoped activity only.
    // Project enumeration is obtained from the same repository as the current shell.
    const projects=(await localOrganizationRepository.listProjects()).items;
    const ids=filter.projectId?[filter.projectId]:projects.map(p=>p.id);
    const pages=await Promise.all(ids.map(async id=>{
      const tasks=(await this.workspace.listTasks(id)).items;
      return Promise.all(tasks.map(t=>this.workspace.activity(t.id)));
    }));
    const seen=new Set<string>();
    let logs=pages.flat(2).flatMap(p=>p.items).filter(log=>{
      if(seen.has(log.id))return false;
      seen.add(log.id);return true;
    });
    const term=filter.q?.toLowerCase().trim();
    logs=logs.filter(log=>
      (!filter.projectId||log.projectId===filter.projectId)&&
      (!filter.level||log.level===filter.level)&&
      (!filter.source||log.source.toLowerCase().includes(filter.source.toLowerCase()))&&
      (!term||[log.message,log.source,log.traceId??'',log.taskId??''].some(t=>t.toLowerCase().includes(term)))&&
      (!filter.since||Date.parse(log.timestamp)>=Date.parse(filter.since))&&(!filter.until||Date.parse(log.timestamp)<=Date.parse(filter.until))&&
      (!filter.traceId||log.traceId===filter.traceId)&&
      (!filter.resourceId||log.message.includes(filter.resourceId)));
    logs.sort((a,b)=>b.timestamp.localeCompare(a.timestamp)||b.id.localeCompare(a.id));
    const cursor=decodeCursor(filter.cursor);const limit=Math.min(Math.max(1,filter.limit??50),maxPage);
    return {items:logs.slice(cursor,cursor+limit),nextCursor:cursor+limit<logs.length?String(cursor+limit):null};
  };
}
export const localSystemRepository=new LocalSystemRepository();
