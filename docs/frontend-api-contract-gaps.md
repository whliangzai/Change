# Frontend API Contract Notes

## 2026-09-04 Jinja2 MVP

- The strategy API provides create, review, and diff endpoints but no paginated
  strategy-version list or detail endpoint. The strategy page therefore creates
  and reviews a version from the supported routes, and only shows the version
  identifiers returned in the current browser session. It does not invent a
  list response.
- The operations API provides manual execution creation but no execution-list
  endpoint. The execution page accepts a selected confirmed plan and displays
  the server response; it does not claim a historical execution ledger list.
- The reports page derives selectable runs from `GET /api/v1/backtests` and
  obtains each result from `GET /api/v1/backtests/{run_id}/report`. There is no
  independent report catalogue endpoint.
- A plan can only be created by the existing daily-flow application service.
  The UI exposes confirmation and manual entry only after the service has
  generated a plan. It never fabricates a plan or changes status locally.

These are intentional minimal compatibility behaviors. Any new list or detail
API must be added as a versioned backend contract with RBAC, audit and tests;
the browser client must not emulate it from strings or mutable local state.
