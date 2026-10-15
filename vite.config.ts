import {defineConfig,loadEnv} from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({mode})=>{
  const publicEnv=loadEnv(mode,process.cwd(),'VITE_');
  // Development proxy shares backend/.env with the backend launcher so the
  // backend host/port have a single source of truth. VITE_BACKEND_URL is an
  // optional override for development against a remote backend.
  const backendEnv=loadEnv(mode,`${process.cwd()}/backend`,'MRLC_');
  const backendHost=(backendEnv.MRLC_HOST||'127.0.0.1').trim();
  const backendPort=(backendEnv.MRLC_PORT||'').trim();
  const explicitBackendUrl=(publicEnv.VITE_BACKEND_URL||'').trim();
  if(!explicitBackendUrl&&!backendPort){
    throw new Error('Missing MRLC_PORT. Copy backend/.env.example to backend/.env and configure the backend port.');
  }
  // 0.0.0.0/:: are bind addresses, not useful proxy destinations.
  const proxyHost=backendHost==='0.0.0.0'?'127.0.0.1':backendHost==='::'?'[::1]':backendHost;
  const backendTarget=explicitBackendUrl||`http://${proxyHost}:${backendPort}`;
  const devHost=(backendEnv.MRLC_DEV_HOST||'').trim();
  const devPortRaw=(backendEnv.MRLC_DEV_PORT||'').trim();
  if(!devHost){
    throw new Error('Missing MRLC_DEV_HOST. Configure it in backend/.env.');
  }
  if(!devPortRaw){
    throw new Error('Missing MRLC_DEV_PORT. Configure it in backend/.env.');
  }
  const devPort=Number(devPortRaw);
  if(!Number.isInteger(devPort)||devPort<1||devPort>65535){
    throw new Error('MRLC_DEV_PORT must be an integer from 1 to 65535.');
  }

  // Vite rejects unknown Host headers by default. Keep allowed hosts in the same
  // backend/.env instead of hard-coding deployment domains in source code.
  const splitCsv=(value:string|undefined)=>
    (value||'').split(',').map(item=>item.trim()).filter(Boolean);
  const configuredHosts=splitCsv(backendEnv.MRLC_DEV_ALLOWED_HOSTS);
  const configuredOrigins=[
    ...splitCsv(backendEnv.MRLC_ALLOWED_ORIGINS),
    ...splitCsv(backendEnv.MRLC_ALLOWED_ORIGIN)
  ];
  const originHosts=configuredOrigins.flatMap(origin=>{
    try{return [new URL(origin).hostname];}catch{return [];}
  });
  const allowedHosts=[...new Set(['localhost','127.0.0.1',...configuredHosts,...originHosts])];

  return {
    plugins:[react()],
    server:{
      host:devHost,
      port:devPort,
      strictPort:true,
      allowedHosts,
      proxy:{'/api':{target:backendTarget,changeOrigin:true}}
    },
    build:{outDir:'dist'},
    define:{
      __MRLC_PUBLIC_CONFIG__:JSON.stringify({
        VITE_DATA_SOURCE:publicEnv.VITE_DATA_SOURCE??'http',
        VITE_API_BASE_URL:publicEnv.VITE_API_BASE_URL??'/',
        VITE_EVENTS_URL:publicEnv.VITE_EVENTS_URL??'/api/v1/events'
      })
    }
  };
});
