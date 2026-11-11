# Architecture and parallel engineering

Proposed stack: Next.js/TypeScript web, PostgreSQL + pgvector (optional local), Python analytics with scikit-learn (UMAP optional), deterministic offline fixtures and reproducible contract tests. An offline demo MUST work without paid APIs or real keys.

```
ATS feeds / user-authorized export → Data Matching (A) → versioned job & profile evidence
                                          ↓
                             Application + Experiments (B)
                                          ↓
                               Analytics & Atlas (C)
                                          ↓
                                    Product UI (D)
                                          ↑
                               Integration & Security (E)
```

A exports normalized job data + match explanations; B consumes jobs and evidence claims, records immutable experiment versions and application events; C consumes vector representations + outcomes, exports geometry diagnostics and transition scenarios; D is a presentation layer and does not reimplement business logic; E validates exact contract compatibility from pinned branches in disposable worktrees.

Frozen baseline on `main`. Breaking interface changes require an RFC and consumer acknowledgment. All PRs are draft and unmerged until owner approval. Never publish personal profiles into a public repository.
