import type {AppearanceSettings, ProjectRuntimeSettings} from '../domain/models';
import {localOrganizationRepository,type OrganizationRepository} from './organizationRepository';
/** Original: index.html L158, L192-193, L254-256, L267, L334-335. */
export interface SettingsRepository {
  getRuntimeSettings(projectId:string):Promise<ProjectRuntimeSettings>;
  updateRuntimeSettings(projectId:string,input:Omit<ProjectRuntimeSettings,'revision'>,expectedRevision:number):Promise<ProjectRuntimeSettings>;
  resetRuntimeSettings(projectId:string,expectedRevision:number):Promise<ProjectRuntimeSettings>;
  getAppearance():Promise<AppearanceSettings>;
  saveAppearance(input:AppearanceSettings):Promise<AppearanceSettings>;
  resetAppearance():Promise<AppearanceSettings>;
  subscribe(listener:()=>void):()=>void;
}
export const runtimeDefaults:Omit<ProjectRuntimeSettings,'revision'>={modelId:'local-default',contextPolicy:'task',sessionPolicy:'resumable',frontierEscalation:'ask'};
export const appearanceDefaults:AppearanceSettings={theme:'strata-dark',motion:'highlights',density:'compact',stageAnimation:'play-once'};
const storageKey='mrlc.settings.demo.v5';
type Db={version:5;runtime:Record<string,ProjectRuntimeSettings>;appearance:AppearanceSettings};
const clone=<T,>(x:T):T=>JSON.parse(JSON.stringify(x)) as T;
const isAllowed=(value:string,values:readonly string[])=>values.includes(value);
function assertRuntime(x:Omit<ProjectRuntimeSettings,'revision'>){
  if(!x.modelId.trim()||x.modelId.length>128)throw new Error('Model ID is required (max 128 characters)');
  if(!isAllowed(x.contextPolicy,['task','project'])||!isAllowed(x.sessionPolicy,['resumable','ephemeral'])||!isAllowed(x.frontierEscalation,['ask','budget','disabled']))throw new Error('Invalid runtime settings');
}
function assertAppearance(x:AppearanceSettings){
  if(!isAllowed(x.theme,['strata-dark','soft-dark'])||!isAllowed(x.motion,['highlights','reduced'])||!isAllowed(x.density,['compact','comfortable'])||!isAllowed(x.stageAnimation,['play-once','static-gold']))throw new Error('Invalid appearance setting');
}
function loadDb():Db{try{const raw=localStorage.getItem(storageKey);if(raw){const x=JSON.parse(raw) as Db;if(x.version===5&&x.runtime&&x.appearance){assertAppearance(x.appearance);return x;}}}catch{/* use defaults */}return {version:5,runtime:{},appearance:clone(appearanceDefaults)};}
export class LocalSettingsRepository implements SettingsRepository {
  constructor(private readonly organization:OrganizationRepository=localOrganizationRepository){}
  private db=loadDb();private listeners=new Set<()=>void>();
  subscribe=(f:()=>void)=>{this.listeners.add(f);return()=>{this.listeners.delete(f);};};
  private changed(){try{localStorage.setItem(storageKey,JSON.stringify(this.db));}catch{/* memory only */}this.listeners.forEach(f=>f());}
  private async assertProject(projectId:string,write=false){const projects=(await this.organization.listProjects()).items;const project=projects.find(x=>x.id===projectId);if(!project)throw new Error('Project not found');if(write&&project.status==='archived')throw new Error('Archived projects cannot change settings');}
  getRuntimeSettings=async(projectId:string)=>{await this.assertProject(projectId);return clone(this.db.runtime[projectId]??{...runtimeDefaults,revision:1});};
  updateRuntimeSettings=async(projectId:string,input:Omit<ProjectRuntimeSettings,'revision'>,expectedRevision:number)=>{
    await this.assertProject(projectId,true);assertRuntime(input);
    const current=this.db.runtime[projectId]??{...runtimeDefaults,revision:1};if(current.revision!==expectedRevision)throw new Error('Settings revision conflict');
    const next={...input,revision:expectedRevision+1};this.db.runtime[projectId]=next;this.changed();return clone(next);
  };
  resetRuntimeSettings=async(projectId:string,expectedRevision:number)=>this.updateRuntimeSettings(projectId,runtimeDefaults,expectedRevision);
  getAppearance=async()=>clone(this.db.appearance);
  saveAppearance=async(input:AppearanceSettings)=>{assertAppearance(input);this.db.appearance=clone(input);this.changed();return clone(input);};
  resetAppearance=async()=>this.saveAppearance(appearanceDefaults);
}
export const localSettingsRepository=new LocalSettingsRepository();
