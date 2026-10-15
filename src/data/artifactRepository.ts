import type { Artifact, ArtifactVersion, EvidenceLink, Page } from '../domain/models';
import { localOrganizationRepository, type OrganizationRepository } from './organizationRepository';
import { localWorkspaceRepository, type WorkspaceRepository } from './workspaceRepository';

/** Discovery: original HTML L123-126, L243, L281-286, L289-295; see INTERFACE_DISCOVERY_PHASE4.md. */
export interface ArtifactRepository {
  listArtifacts(projectId: string): Promise<Page<Artifact>>;
  getArtifact(artifactId: string): Promise<Artifact>;
  uploadArtifact(projectId: string, file: File, taskId?: string): Promise<Artifact>;
  listVersions(artifactId: string): Promise<Page<ArtifactVersion>>;
  listEvidence(artifactId: string): Promise<Page<EvidenceLink>>;
  getContent(artifactId: string, versionId?: string): Promise<Blob | null>;
  subscribe(listener: () => void): () => void;
}

type StoredArtifact = { artifact: Artifact; versions: ArtifactVersion[]; content?: Blob; oldContents: Record<string, Blob>; evidence: EvidenceLink[] };
const DEMO_PROJECT='project-1';
const seedNames=['proof.tex','theorem_notes.md','numerical_check.ipynb','evidence_bundle.zip'];
const initialDate='2026-10-14T00:00:00+08:00';
function seed(): StoredArtifact[] { return seedNames.map((name,i)=>{
  const id=`demo-artifact-${i+1}`;
  const artifact: Artifact={id,projectId:DEMO_PROJECT,name,mimeType:name.endsWith('.zip')?'application/zip':'text/plain',version:'v0.1',status:'partial',createdAt:initialDate,contentAvailable:false,demoMetadata:true};
  return {artifact,versions:[{id:`${id}-v1`,artifactId:id,version:'v0.1',createdAt:initialDate,contentAvailable:false}],oldContents:{},evidence:[]};
}); }
const copy=<T,>(value:T):T=>structuredClone(value);
const makeId=()=>typeof crypto!=='undefined'&&typeof crypto.randomUUID==='function'?crypto.randomUUID():`item-${Date.now()}-${Math.random().toString(36).slice(2)}`;

/** A single IndexedDB record stores metadata, version history and real Blob bytes atomically. */
export class LocalArtifactRepository implements ArtifactRepository {
  private listeners=new Set<()=>void>();
  private memory=new Map<string, StoredArtifact>(seed().map(x=>[x.artifact.id,x]));
  private dbPromise: Promise<IDBDatabase> | null=null;
  readonly storageMode: 'indexeddb'|'memory' = typeof indexedDB==='undefined'?'memory':'indexeddb';
  constructor(private readonly organization:OrganizationRepository=localOrganizationRepository, private readonly workspace:WorkspaceRepository=localWorkspaceRepository) {}
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return ()=>{this.listeners.delete(listener);};};
  private notify(){this.listeners.forEach(listener=>listener());}
  private db():Promise<IDBDatabase>{
    if(this.dbPromise)return this.dbPromise;
    this.dbPromise=new Promise((resolve,reject)=>{
      const request=indexedDB.open('mrlc.artifacts.demo.v4',1);
      request.onupgradeneeded=()=>{
        const store=request.result.createObjectStore('artifacts',{keyPath:'artifact.id'});
        for(const item of seed())store.put(item);
      };
      request.onsuccess=()=>resolve(request.result);
      request.onerror=()=>reject(request.error??new Error('Unable to open IndexedDB'));
    });
    return this.dbPromise;
  }
  private async readAll():Promise<StoredArtifact[]>{
    if(this.storageMode==='memory')return [...this.memory.values()].map(copy);
    const db=await this.db();return new Promise((resolve,reject)=>{
      const tx=db.transaction('artifacts','readonly');const request=tx.objectStore('artifacts').getAll();
      request.onsuccess=()=>resolve(request.result as StoredArtifact[]);
      request.onerror=()=>reject(request.error??new Error('IndexedDB list failed'));
    });
  }
  private async read(id:string):Promise<StoredArtifact>{
    let result:StoredArtifact|undefined;
    if(this.storageMode==='memory')result=this.memory.get(id);
    else {const db=await this.db();result=await new Promise<StoredArtifact|undefined>((resolve,reject)=>{
      const tx=db.transaction('artifacts','readonly');const request=tx.objectStore('artifacts').get(id);
      request.onsuccess=()=>resolve(request.result as StoredArtifact|undefined);
      request.onerror=()=>reject(request.error??new Error('IndexedDB read failed'));
    });}
    if(!result)throw new Error('Artifact not found');return copy(result);
  }
  private async write(item:StoredArtifact):Promise<void>{
    if(this.storageMode==='memory'){this.memory.set(item.artifact.id,copy(item));this.notify();return;}
    const db=await this.db();await new Promise<void>((resolve,reject)=>{
      const tx=db.transaction('artifacts','readwrite');tx.objectStore('artifacts').put(item);
      tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error??new Error('Unable to store file bytes'));
      tx.onabort=()=>reject(tx.error??new Error('Artifact transaction aborted'));
    });this.notify();
  }
  listArtifacts=async(projectId:string)=>({items:(await this.readAll()).filter(x=>x.artifact.projectId===projectId).map(x=>x.artifact)});
  getArtifact=async(id:string)=> (await this.read(id)).artifact;
  listVersions=async(id:string)=>({items:(await this.read(id)).versions});
  listEvidence=async(id:string)=>({items:(await this.read(id)).evidence});
  getContent=async(id:string,versionId?:string)=>{
    const entry=await this.read(id);
    return versionId ? entry.versions[0]?.id===versionId && entry.content ? entry.content : entry.oldContents[versionId]??null : entry.content??null;
  };
  uploadArtifact=async(projectId:string,file:File,taskId?:string)=>{
    if(!file || !file.name.trim())throw new Error('Choose a file');
    if(file.size>25*1024*1024)throw new Error('Demo file limit: 25 MiB per upload');
    const p=(await this.organization.listProjects()).items.find(x=>x.id===projectId);
    if(!p||p.status==='archived')throw new Error('Cannot upload to missing or archived project');
    if(taskId){const tasks=(await this.workspace.listTasks(projectId)).items;if(!tasks.some(t=>t.id===taskId))throw new Error('Linked task does not belong to this project');}
    const all=await this.readAll();const previous=all.find(x=>x.artifact.projectId===projectId&&x.artifact.name===file.name);
    const artifactId=previous?.artifact.id??makeId();const n=(previous?.versions.filter(v=>v.contentAvailable).length??0)+1;
    const version=`v${n}.0`;const versionId=makeId();const createdAt=new Date().toISOString();
    // Hash is only provided if actually computed, never a fabricated checksum.
    let checksum: string|undefined;
    if(typeof crypto!=='undefined'&&crypto.subtle){const bytes=await file.arrayBuffer();const digest=await crypto.subtle.digest('SHA-256',bytes);checksum=[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');}
    const artifact:Artifact={id:artifactId,projectId,taskId:taskId??previous?.artifact.taskId,name:file.name,mimeType:file.type||'application/octet-stream',version,status:'partial',sizeBytes:file.size,checksum,createdAt,contentAvailable:true,demoMetadata:false};
    const oldContents=previous?.oldContents??{};
    if(previous?.content && previous.versions[0])oldContents[previous.versions[0].id]=previous.content;
    const versions:ArtifactVersion[]=[{id:versionId,artifactId,version,sizeBytes:file.size,checksum,createdAt,contentAvailable:true},...(previous?.versions??[])];
    await this.write({artifact,versions,oldContents,content:file,evidence:previous?.evidence??[]});
    return artifact;
  };
}
export const localArtifactRepository=new LocalArtifactRepository();
