# Safety and data-source constraints

**Repository visibility:** PUBLIC at bootstrap. No personal/private data may be committed. The owner should switch to PRIVATE before integrating any personal CV, LinkedIn export, contacts, application outcomes, or credentials.

**LinkedIn:** No scraping, unofficial API calls, CAPTCHA bypass, mass messaging, auto-applying, or assertions that OIDC grants access to connections and jobs. Support manual data export import, explicit consent, and supported API endpoints only when authorized.

**ATS:** Use public documentation of Greenhouse, Lever, Ashby or other authorized boards; obey terms and source restrictions; attach provenance, freshness, URL and expiry. In tests use fictitious postings and no network.

**Privacy/security:** Separate identity vault from public code; never place exports in repository, logs or PR comments. Default deny for application submission, outbound messages, data retention beyond purpose, paid model calls, external telemetry and deployment. Add threat tests for malicious job descriptions, prompt injection, CSV/HTML injection, SSRF via source URLs, secret redaction, cross-user data access and consent changes.

**Epistemic guardrails:** Model fit is not hiring probability; source evidence is not legal assurance; observational application outcomes cannot be represented as randomized experiments. Require preregistered assignment, holdout analysis and missingness reporting.
