"""Read-only GraphQL gateway.

Layered on top of the REST API: writes still go through REST (they own the
conflict envelope + SELECT FOR UPDATE transactions); GraphQL only exposes
queries. Resolvers reuse `app/logic/*` so business rules stay single-sourced.
"""
