# Source compliance matrix — lane A (CT-2026-11-10-B01)

**Status in this batch: no source is contacted.** Every adapter parses an already-captured
payload. No module in `connectors/` or `packages/data-matching/` opens a socket
(`test_no_network_modules_imported_by_connectors` guards this). `LiveFetchDisabled` is the
only fetcher besides the fixture loader and always refuses with `network_disabled`.
Turning on any live source is an **owner decision** (AGENTS.md §Safety, ADR-0001).

Payload shapes were written from my knowledge of each vendor's public documentation.
They were **not** checked against live endpoints or re-read during this batch (no network
access was used). Before any live use, a person must confirm each row against the
vendor's current documentation and terms. That check is an open owner action, not a
completed one.

| Source (`JobPosting.source`) | Interface the adapter models | Auth needed for read | Status in B01 | Conditions before enabling live use (owner) | Data kept | Data never kept |
|---|---|---|---|---|---|---|
| `greenhouse` | Job Board API, list endpoint with `content=true` (`{"jobs": [...], "meta": {...}}`; HTML entity-escaped `content`, `pay_input_ranges` in cents) | none for public boards | adapter + recorded **synthetic** fixture; never called | confirm current Job Board API terms; only boards the employer publishes publicly; honour rate limits; identify client generically; store `license_note` per capture | posting fields, URL, capture time, payload SHA-256 | applicant data (the read API exposes none), cookies, tracking params |
| `lever` | Postings API `?mode=json` (array of postings; `createdAt` epoch ms; `lists[]` HTML; optional `salaryRange`) | none for public postings | adapter + synthetic fixture; never called | same as above; do **not** use the apply/POST endpoints (outside scope: no auto-apply) | as above | as above |
| `ashby` | public job-board posting API with `includeCompensation=true` (`{"jobs": [...]}`; `isListed`; compensation `summaryComponents`) | none for public boards | adapter + synthetic fixture; never called | same as above | as above; equity/bonus components ignored (not base pay) | as above |
| `synthetic` | lane A native format `{"postings": [...]}` | n/a | used by the seeded corpus generator and tests | none: fictional data only, `.example` hosts, `synthetic: true` | everything (fictional) | — |
| `user-import` | same native format, for a file the user exports and supplies by hand | n/a | adapter exists; **disabled for real data while the repo is PUBLIC** | repo must be PRIVATE; explicit user consent; retention policy decided | posting fields only | profile, contacts, application history |
| `other-permitted` | none | — | no adapter → `unsupported_source` | owner names the source and its terms first | — | — |
| LinkedIn | none | — | **no adapter; rejected as `unsupported_source`** (`test_linkedin_is_not_a_supported_source`) | not planned: no scraping, no unofficial API, no CAPTCHA/rate-limit bypass (SECURITY_AND_DATA_SOURCES.md) | — | everything |

## Controls that apply to every source
- **URLs are never fetched.** They are only validated before storage (`textsafe.url_problem`): http(s) only, no userinfo, no IP literals, private/link-local/metadata ranges, numeric or single-label hosts, internal suffixes, unusual ports or non-ASCII/punycode hosts. Cross-checked against lane E's 42-case attack list (see HANDOFF). DNS rebinding is out of scope until fetching is authorised; it then needs a connect-time check.
- **Content is untrusted data.** HTML → visible text only; scripts, hidden elements and comments are dropped; control, bidi and zero-width characters are stripped; instructions aimed at automated readers are flagged (`prompt-injection-suspected`), and flagged postings are refused by the ranker by default.
- **Provenance.** Each stored version records source, board, source id, capture time, payload SHA-256 and content hash in a hash-chained ledger (`JobStore.ledger.verify()`).
- **Freshness.** A posting is `active` only if it was listed at a capture at most 14 days old; after that it becomes `unknown`, never "still open".
- **No compensation inference.** Pay is only surfaced when the source states an annual range with an explicit currency. It is never annualized, converted or guessed, and it is always `verified: false`.
