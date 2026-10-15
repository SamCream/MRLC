import type {Services} from '../api/services';
import type {SystemRepository} from './systemRepository';
import type {SettingsRepository} from './settingsRepository';
const noLiveEvents=()=>()=>{};
/** Candidate HTTP routes; disabled until backend authentication and contracts are reconciled. */
export const httpSystemRepository=(services:Services):SystemRepository=>({
  subscribe:noLiveEvents,
  getHealth:services.system.health,
  runHealthCheck:services.system.checkHealth,
  getTopology:services.system.topology,
  listNodes:services.system.nodes,
  listAccelerators:services.system.accelerators,
  listLogs:services.system.logs,
  listLogViews:services.system.logViews,
  saveLogView:(name,filter)=>services.system.createLogView({name,filter}),
  deleteLogView:services.system.deleteLogView,
});
/** Runtime and appearance preferences are now server-authoritative. */
export const httpSettingsRepository=(services:Services):SettingsRepository=>({
  subscribe:noLiveEvents,
  getRuntimeSettings:services.system.runtimeSettings,
  updateRuntimeSettings:services.system.updateRuntimeSettings,
  resetRuntimeSettings:services.system.resetRuntimeSettings,
  getAppearance:services.system.appearance,
  saveAppearance:services.system.saveAppearance,
  resetAppearance:services.system.resetAppearance,
});
