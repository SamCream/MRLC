import type { Services } from '../api/services';
import type { OrganizationRepository } from './organizationRepository';
/** HTTP organization adapter. SSE subscription is supplied by the shared invalidation wrapper. */
export function httpOrganizationRepository(api: Services): OrganizationRepository {
  return {
    subscribe: () => () => {}, // Replaced by withRemoteInvalidation() at the composition root.
    listProjects: api.projects.list,
    createProject: api.projects.create,
    updateProject: api.projects.update,
    archiveProject: api.projects.archive,
    listRoles: api.roles.list,
    createRole: api.roles.create,
    updateRole: api.roles.update,
    transitionRole: api.roles.lifecycle,
    listAgents: api.agents.list,
    listAgentSessions: api.agents.sessions,
    assignAgent: api.agents.assign,
    parkAgent: api.agents.park,
    resumeAgent: api.agents.resume,
  };
}
