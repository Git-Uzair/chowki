# Language-Neutral Protocol Specifications

This directory contains the language-neutral protocol specifications and JSON Schemas for `chowki` (`spec/v1/`), acting as the single source of truth across all SDK implementations (ADR-001).

Automated model code generation for Python (Pydantic v2) and TypeScript models is scheduled for Phase 2 (`docs/plans/00-roadmap.md`).

Until the schemas here are complete, the normative cross-SDK contract — canonical hashing, redaction placeholders, step identity, token format, and the rest of what every SDK must reproduce byte-for-byte — is `docs/research/07-cross-sdk-parity.md`, extracted from the verified Python reference implementation. Phase 2 formalises that document into `spec/v1/`.
