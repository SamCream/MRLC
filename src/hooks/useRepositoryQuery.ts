import {useEffect,useRef,useState} from 'react';
import {repositories} from '../data/repositories';
export interface ObservableRepository {subscribe(listener:()=>void):()=>void;}
/** Query hook with stale-response suppression on dependency changes and refresh races.
 * The previous result is cleared when the scoped query changes: never flash another project's data.
 */
export function useRepositoryQuery<T>(load:()=>Promise<T>,deps:readonly unknown[],repository:ObservableRepository=repositories.workspace){
  const [data,setData]=useState<T|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [loading,setLoading]=useState(true);
  const newest=useRef(0);
  useEffect(()=>{
    let active=true;
    const refresh=()=>{
      const requestId=++newest.current;
      setLoading(true);
      load().then(result=>{
        if(active&&requestId===newest.current){setData(result);setError(null);setLoading(false);}
      }).catch(e=>{
        if(active&&requestId===newest.current){setError(e instanceof Error?e.message:String(e));setData(null);setLoading(false);}
      });
    };
    setData(null);setError(null);
    refresh();
    const unsubscribe=repository.subscribe(refresh);
    return()=>{active=false;newest.current+=1;unsubscribe();};
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[...deps,repository]);
  return {data,error,loading};
}
