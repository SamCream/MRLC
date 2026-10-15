import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { Project } from '../domain/models';
import {repositories} from '../data/repositories';
const organization=repositories.organization;
import { useRepositoryQuery } from '../hooks/useRepositoryQuery';

export const pageIds = ['overview','workspace','tasks','artifacts','projects','agents','roles','approvals','compute','infrastructure','logs','settings'] as const;
export type PageId = typeof pageIds[number];
const validPage = (value: string): value is PageId => pageIds.some(x => x===value);
type ShellState = { page: PageId; navigate: (page: PageId) => void; projectId: string; setProjectId: (id: string) => void; project: Project | null; projects: Project[]; loading: boolean; error: string | null };
const AppContext = createContext<ShellState | null>(null);
const PROJECT_KEY = 'mrlc.selectedProject.v3';
function savedProject(): string { try { return localStorage.getItem(PROJECT_KEY) || 'project-1'; } catch { return 'project-1'; } }
function currentPage(): PageId { const value=location.hash.slice(1);return validPage(value)?value:'overview'; }

export function AppProvider({children}:{children: ReactNode}) {
  const [page,setPage] = useState<PageId>(currentPage);
  const [projectId,setProjectIdState] = useState(savedProject);
  const result = useRepositoryQuery(()=>organization.listProjects(),[],organization);
  const projects = result.data?.items ?? [];
  const project = projects.find(p=>p.id===projectId && p.status!=='archived') ?? null;
  const navigate = useCallback((target: PageId) => {
    setPage(target);
    if(location.hash!==`#${target}`) location.hash=target;
    document.querySelector('.main')?.scrollTo({top:0});
  },[]);
  useEffect(()=>{
    const onHash=()=>{const target=currentPage();setPage(target);};
    window.addEventListener('hashchange',onHash);
    return ()=>{window.removeEventListener('hashchange',onHash);};
  },[navigate]);
  useEffect(()=>{
    if(result.loading)return;
    if(!project && projects.some(p=>p.status!=='archived')) {
      setProjectIdState(projects.find(p=>p.status!=='archived')!.id);
    }
  },[project,result.loading,projects]);
  const setProjectId = useCallback((id:string)=>setProjectIdState(id),[]);
  useEffect(()=>{try{localStorage.setItem(PROJECT_KEY,projectId);}catch{/* memory-only */}},[projectId]);
  const value = useMemo(()=>({page,navigate,projectId,project,projects,loading:result.loading,error:result.error,setProjectId}),[page,navigate,projectId,project,projects,result.loading,result.error,setProjectId]);
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
export function useAppShell(): ShellState {
  const value=useContext(AppContext); if(!value)throw new Error('useAppShell requires AppProvider'); return value;
}
