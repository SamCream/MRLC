import type { WorkspaceRepository } from './workspaceRepository';
import type { Services } from '../api/services';
/** Swappable backend adapter. Do not activate until endpoints match server contracts. */
export function httpWorkspaceRepository(api: Services): WorkspaceRepository {
  return {
    subscribe: () => () => {}, // remote updates must be delivered by SSE refresh/revalidation
    listChannels: api.channels.list,
    createChannel: api.channels.create,
    listMessages: api.channels.messages,
    sendMessage: api.channels.send,
    listTasks: api.tasks.list,
    createTask: api.tasks.create,
    reviewTask: api.tasks.review,
    verifyCriterion: api.tasks.verifyCriterion,
    acceptTask: api.tasks.accept,
    dispatchTask: api.tasks.dispatch,
    respondToChannel: api.channels.respond,
    activity: api.tasks.activity,
  };
}
