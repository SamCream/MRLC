import type {RuntimeEvent} from '../domain/models';

/** Shared HTTP read invalidation. Calling notify after a resolved write is never a server audit event. */
export class InvalidationBus {
  private readonly listeners=new Set<()=>void>();
  private readonly ids=new Set<string>();
  private source: EventSource|null=null;
  private readonly recentIds:string[]=[];
  private sourceFactory:((url:string)=>EventSource)|null=null;
  private url:string|undefined;
  private readonly maxSeen=512;
  constructor(url?:string,factory?:(url:string)=>EventSource){this.url=url;this.sourceFactory=factory??null;}
  subscribe=(listener:()=>void)=>{
    this.listeners.add(listener);
    if(this.listeners.size===1)this.connect();
    return ()=>{this.listeners.delete(listener);if(!this.listeners.size){this.source?.close();this.source=null;}};
  };
  notify=()=>{for(const listener of Array.from(this.listeners))listener();};
  /** Invalid messages are ignored, and repeated event IDs trigger at most one refresh. */
  receive(data:unknown):boolean{
    if(!data || typeof data!=='object')return false;
    const event=data as Partial<RuntimeEvent>;
    if(typeof event.id!=='string'||!event.id||typeof event.type!=='string'||!event.type||typeof event.occurredAt!=='string'||!event.occurredAt||
       (event.projectId!==undefined&&typeof event.projectId!=='string')||!('payload' in event))return false;
    if(this.ids.has(event.id))return false;
    this.ids.add(event.id);this.recentIds.push(event.id);
    if(this.recentIds.length>this.maxSeen)this.ids.delete(this.recentIds.shift()!);
    this.notify();return true;
  }
  private connect(){
    if(!this.url||this.source||(!this.sourceFactory&&typeof EventSource==='undefined'))return;
    try{
      const source=this.sourceFactory?this.sourceFactory(this.url):new EventSource(this.url,{withCredentials:true});
      this.source=source;
      source.onmessage=(message)=>{try{this.receive(JSON.parse(message.data));}catch{/* invalid/unsupported payload ignored */}};
      // Native EventSource reconnects. After a transport failure re-read snapshots once the stream reopens.
      source.onopen=()=>this.notify();
      source.onerror=()=>{/* HTTP queries still work; SSE is best-effort */};
    }catch{/* no verified SSE endpoint; regular HTTP queries still expose errors */}
  }
}

/** Wrap an HTTP repository so resolved writes update native React query caches immediately,
 * even when the future server has no SSE stream. No notification is sent on a rejected write.
 */
export function withRemoteInvalidation<T extends {subscribe(listener:()=>void):()=>void}>(
  repository:T,bus:InvalidationBus,mutations:readonly (keyof T & string)[]
):T{
  const writes=new Set<string>(mutations);
  return new Proxy(repository,{
    get(target,key,receiver){
      if(key==='subscribe')return bus.subscribe;
      const result=Reflect.get(target,key,receiver);
      if(typeof key!=='string'||!writes.has(key)||typeof result!=='function')return result;
      return async (...args:unknown[])=>{
        const output=await result.apply(target,args);
        bus.notify();return output;
      };
    }
  });
}
