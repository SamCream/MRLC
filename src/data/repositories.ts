import {ApiClient} from '../api/client';
import {createServices} from '../api/services';
import {localOrganizationRepository} from './organizationRepository';
import {localWorkspaceRepository} from './workspaceRepository';
import {localArtifactRepository} from './artifactRepository';
import {localApprovalRepository} from './approvalRepository';
import {localComputeRepository} from './computeRepository';
import {localSystemRepository} from './systemRepository';
import {localSettingsRepository} from './settingsRepository';
import {localAuthRepository,httpAuthRepository} from './authRepository';
import {httpOrganizationRepository} from './httpOrganizationRepository';
import {httpWorkspaceRepository} from './httpWorkspaceRepository';
import {httpArtifactRepository,httpApprovalRepository,httpComputeRepository} from './httpPhase4Repositories';
import {httpSystemRepository,httpSettingsRepository} from './httpSystemRepository';
import {InvalidationBus,withRemoteInvalidation} from './remoteInvalidation';
import {localInfraRepository,httpInfraRepository} from './infraRepository';

/** A composition root, rather than each page hard-coding a local mock. */
export type DataSource='mock'|'http';
export interface RuntimeConfiguration {mode:DataSource;baseUrl:string;eventsUrl?:string;}
export function parseRuntimeConfiguration(env:Record<string,string|undefined>):RuntimeConfiguration{
  const mode=env.VITE_DATA_SOURCE??'mock';
  if(mode!=='mock'&&mode!=='http')throw new Error('VITE_DATA_SOURCE must be mock or http');
  if(mode==='mock')return {mode:'mock',baseUrl:''};
  const baseUrl=env.VITE_API_BASE_URL?.trim();
  if(!baseUrl)throw new Error('VITE_API_BASE_URL is required when VITE_DATA_SOURCE=http; no silent mock fallback');
  // Explicit http:// is supported for local development only; TLS is recommended for remote deployments.
  if(!baseUrl.startsWith('/')&&!/^https?:\/\//.test(baseUrl))throw new Error('VITE_API_BASE_URL must be an HTTP(S) URL or same-origin relative path');
  const eventsUrl=env.VITE_EVENTS_URL?.trim();
  if(eventsUrl&&!eventsUrl.startsWith('/')&&!/^https?:\/\//.test(eventsUrl))throw new Error('Invalid VITE_EVENTS_URL');
  return {mode:'http',baseUrl,eventsUrl:eventsUrl||undefined};
}

export function createRepositoryBundle(config:RuntimeConfiguration){
  if(config.mode==='mock')return {
    mode:config.mode,
    organization:localOrganizationRepository,
    workspace:localWorkspaceRepository,
    artifacts:localArtifactRepository,
    approvals:localApprovalRepository,
    compute:localComputeRepository,
    system:localSystemRepository,
    settings:localSettingsRepository,
    auth:localAuthRepository,
    infra:localInfraRepository,
    invalidation:null as InvalidationBus|null,
  };
  const services=createServices(new ApiClient({baseUrl:config.baseUrl}));
  const bus=new InvalidationBus(config.eventsUrl);
  return {
    mode:config.mode,
    organization:withRemoteInvalidation(httpOrganizationRepository(services),bus,['createProject','updateProject','archiveProject','createRole','updateRole','transitionRole','assignAgent','parkAgent','resumeAgent']),
    workspace:withRemoteInvalidation(httpWorkspaceRepository(services),bus,['createChannel','sendMessage','createTask','reviewTask','dispatchTask','respondToChannel','verifyCriterion','acceptTask']),
    artifacts:withRemoteInvalidation(httpArtifactRepository(services),bus,['uploadArtifact']),
    approvals:withRemoteInvalidation(httpApprovalRepository(services),bus,['decide']),
    compute:withRemoteInvalidation(httpComputeRepository(services),bus,['pauseSlot','resumeSlot','releaseSlot']),
    system:withRemoteInvalidation(httpSystemRepository(services),bus,['runHealthCheck','saveLogView','deleteLogView']),
    settings:withRemoteInvalidation(httpSettingsRepository(services),bus,['updateRuntimeSettings','resetRuntimeSettings','saveAppearance','resetAppearance']),
    auth:httpAuthRepository(services),
    infra:httpInfraRepository(services),
    invalidation:bus,
  };
}
// Vite replaces this define at bundle time. Node repository tests intentionally default to mock.
// No tokens/secrets are put into public Vite defines.
declare const __MRLC_PUBLIC_CONFIG__:Record<string,string|undefined>;
const injectedEnv=typeof __MRLC_PUBLIC_CONFIG__==='undefined'?{}:__MRLC_PUBLIC_CONFIG__;
const cfg=parseRuntimeConfiguration(injectedEnv);
export const runtimeConfig=cfg;
export const repositories=createRepositoryBundle(cfg);
