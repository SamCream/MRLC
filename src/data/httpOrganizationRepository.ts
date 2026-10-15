import type { Services } from '../api/services';
import type { OrganizationRepository } from './organizationRepository';
/** Future adapter, disabled by default until confirmed backend routes and auth exist. */
export function httpOrganizationRepository(api: Services): OrganizationRepository {
  return {
    subscribe: () => () => {}, // SSE revalidation must be wired before production
    listProjects: api.projects.list,
    createProject: api.projects.create,
    updateProject: api.projects.update,
    archiveProject: api.projects.archive,
    listRoles: api.roles.list,
    createRole: api.roles.create,
    updateRole: api.roles.update,
    transitionRole: api.roles.lifecycle,
    listAgents: api.agents.list,
    assignAgent: api.agents.assign,
    parkAgent: api.agents.park,
    resumeAgent: api.agents.resume,
  };
}
