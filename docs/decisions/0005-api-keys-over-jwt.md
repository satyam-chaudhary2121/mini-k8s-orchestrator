# ADR 0005: API keys with RBAC instead of JWT

**Status:** accepted

## Problem
Need authentication for users and workers and role-based authorisation without an identity provider.

## Options
1. JWT issued by the control plane. 2. API keys (hashed in DB, secrets in env). 3. mTLS.

## Chosen solution
API keys, SHA-256 hashed at rest, mapped to four roles; `AuthenticationProvider` abstraction.

## Reason
Simplest mechanism that is genuinely secure for service-to-service use, trivially rotatable, no token-issuance/expiry machinery or signing-key management. Keys are high-entropy so unsalted SHA-256 is appropriate.

## Trade-offs
No per-user identity, expiry or revocation list beyond restart; JWT/OIDC would be added behind the same interface.
