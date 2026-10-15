import {repositories} from '../data/repositories';
import {useEffect} from 'react';
import {useRepositoryQuery} from '../hooks/useRepositoryQuery';

const repo=repositories.settings;
/** Body class strategy preserves the original HTML prototype CSS without a second theme engine. */
export function useAppearance(){
  const result=useRepositoryQuery(()=>repo.getAppearance(),[],repo);
  useEffect(()=>{
    const settings=result.data;if(!settings)return;
    document.body.classList.toggle('theme-soft',settings.theme==='soft-dark');
    document.body.classList.toggle('reduced-motion',settings.motion==='reduced');
    document.body.classList.toggle('comfortable',settings.density==='comfortable');
    document.body.classList.toggle('static-stage',settings.stageAnimation==='static-gold');
  },[result.data]);
}
