import type {AuthAccount,LoginTokenResult,LoginTokenStatus} from '../domain/models';
import type {Services} from '../api/services';

export interface AuthRepository {
  getCurrentAccount():Promise<AuthAccount>;
  getTokenStatus():Promise<LoginTokenStatus>;
  rotateToken(token?:string):Promise<LoginTokenResult>;
  revokeToken():Promise<void>;
  subscribe(listener:()=>void):()=>void;
}

class LocalAuthRepository implements AuthRepository {
  private readonly listeners=new Set<()=>void>();
  getCurrentAccount=async()=>({id:'local-demo',username:'local-demo',role:'administrator',displayName:'Local demo'});
  getTokenStatus=async()=>({managedBy:'unavailable' as const,configured:false,canManage:false});
  rotateToken=async()=>{throw new Error('Login tokens require the HTTP backend');};
  revokeToken=async()=>{throw new Error('Login tokens require the HTTP backend');};
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
}

export function httpAuthRepository(services:Services):AuthRepository {
  const listeners=new Set<()=>void>();
  const notify=()=>listeners.forEach(listener=>listener());
  return {
    getCurrentAccount:()=>services.auth.me(),
    getTokenStatus:()=>services.auth.tokenStatus(),
    rotateToken:async(token?:string)=>{const result=await services.auth.rotateToken(token);notify();return result;},
    revokeToken:async()=>{await services.auth.revokeToken();notify();},
    subscribe:(listener)=>{listeners.add(listener);return()=>{listeners.delete(listener);};},
  };
}

export const localAuthRepository:AuthRepository=new LocalAuthRepository();
