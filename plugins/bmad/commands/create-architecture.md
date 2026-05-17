Design system architecture with key design decisions:

1. **System Overview** — Purpose, scope, key stakeholders
2. **Component Model** — Services, modules, their responsibilities and interfaces
3. **Data Model** — Core entities, relationships, storage strategy
4. **API Design** — Endpoints, protocols, authentication, rate limiting
5. **Security Model** — Threat model, auth mechanism, data protection
6. **Deployment Architecture** — Infrastructure, CI/CD, scaling strategy

For each component document:
- Responsibility and ownership
- Dependencies and integration points
- Key design decisions with rationale
- Error handling and resilience patterns

Use Architecture Decision Records (ADRs) for key decisions: trade-offs, alternatives considered, chosen approach.

Use `delegate_task` for parallel section design if the system has 3+ components.

Write to `planning-artifacts/architecture-{project_name}-{date}.md`.
