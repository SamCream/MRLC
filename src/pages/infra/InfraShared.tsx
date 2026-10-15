import type {ReactNode} from 'react';

export function infraTime(value?:string|null){
  if(!value)return '—';
  const d=new Date(value);
  return Number.isNaN(d.getTime())?value:d.toLocaleString();
}
export function infraSeconds(value?:number|null){
  if(value===null||value===undefined)return '—';
  if(value<60)return String(value)+'s';
  const min=Math.floor(value/60),sec=value%60;
  return sec?String(min)+'m '+String(sec)+'s':String(min)+'m';
}
export function infraNum(value:number|undefined|null){
  return value===null||value===undefined?'—':value.toLocaleString();
}
export function infraBadge(value:string){
  const upper=value.toUpperCase();
  if(['HEALTHY','ACTIVE','COMPLETED','PRESENT','INTEGRATED','SUCCEEDED','PUBLISHED'].some(x=>upper.includes(x)))return 'green';
  if(['UNKNOWN','PENDING','QUEUED','PARKED','WAIT','REQUESTED','PREPARED'].some(x=>upper.includes(x)))return 'amber';
  if(['FAILED','BROKEN','MISSING','DEAD','ERROR','MANUAL'].some(x=>upper.includes(x)))return 'red';
  if(['SUPERSEDED','COMPENSATED'].some(x=>upper.includes(x)))return 'violet';
  return '';
}
export function InfraEmpty({children}:{children:ReactNode}){
  return <div className="infra-empty">{children}</div>;
}
export function InfraError({error}:{error:string|null}){
  return error?<p className="infra-error" role="alert">{error}</p>:null;
}
export function InfraMetric({label,value,meta,tone}:{label:string;value:string|number;meta?:string;tone?:string}){
  return <div className="card infra-metric"><div className="infra-metric-label">{label}</div><div className={'infra-metric-value '+(tone??'')}>{value}</div>{meta&&<div className="summary-meta">{meta}</div>}</div>;
}
export function InfraPageHead({title,description,tag}:{title:string;description:string;tag?:string}){
  return <div className="page-head"><div><h1>{title}{tag&&<span className="badge cyan" style={{marginLeft:9}}>{tag}</span>}</h1><p>{description}</p></div></div>;
}
