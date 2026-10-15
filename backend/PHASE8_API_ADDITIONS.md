# Phase 8 API additions

## Identity/RBAC
- `GET /api/v1/auth/permissions`
- `GET/POST /api/v1/auth/users`
- `PATCH /api/v1/auth/users/{userId}`
- `GET /api/v1/auth/users/{userId}/projects`
- `PUT/DELETE /api/v1/auth/users/{userId}/projects/{projectId}`

Login accepts `{ "username": "owner", "password": "..." }`; username remains optional for backward compatibility.

### Dedicated browser login tokens (v0.8.1)
- `GET /api/v1/auth/token` with `X-MRLC-Token` establishes the same HttpOnly session as password login while using an independent credential.
- `GET /api/v1/auth/me/token` reports token management state.
- `POST /api/v1/auth/me/token` generates or replaces the current non-admin account token; plaintext is returned once and only a digest is stored.
- `DELETE /api/v1/auth/me/token` revokes the current non-admin account token.
- Administrator URL token is `MRLC_ADMIN_TOKEN` in `backend/.env` and cannot be read or mutated through the API.

## Runtime recovery
- `POST /api/v1/runtime/jobs/{jobId}/cancel`
- `POST /api/v1/runtime/jobs/{jobId}/retry`
- `GET /api/v1/runtime/jobs/{jobId}/context`

Retry reuses the persisted logical ContextSnapshot. It does not claim to restore a provider KV checkpoint.

## Audit / telemetry
- `GET /api/v1/system/audit/verify`
- `GET /api/v1/system/host-metrics`
- `POST /api/v1/system/logs/export` accepts `format=audit_bundle`.

## Coordination/reporting
- `GET /api/v1/projects/{projectId}/coordination`
- `POST /api/v1/projects/{projectId}/reports`

## Typed approval effect
`POST /api/v1/approvals` may include:

```json
{
  "effect": {
    "kind": "grant_role_permission | archive_role | set_project_budget | update_project_setting",
    "payload": {}
  }
}
```

The effect runs in the same SQLite transaction as an approve/reduce-scope decision.
