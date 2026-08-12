# chowki (Node / TypeScript) — placeholder

**This package has no implementation yet.** It exists so the name resolves to the real
project instead of to nothing, and so anyone who finds it is pointed at the SDK that does
work today.

`chowki` is an in-process control plane for LLM agents: durable state, human approval
gates, secret redaction, and runaway-loop guardrails, added to agent code you already
have — as decorators, with no server, worker pool, or sidecar.

## Use the Python SDK today

```bash
pip install chowki
```

- PyPI: <https://pypi.org/project/chowki/>
- Source and documentation: <https://github.com/Git-Uzair/chowki>

## Status of the Node SDK

Roadmap phase 3, tracked in
[`docs/plans/00-roadmap.md`](https://github.com/Git-Uzair/chowki/blob/main/docs/plans/00-roadmap.md).

It is not a port-when-we-get-round-to-it: the cross-SDK contract is already written down.
[`docs/research/07-cross-sdk-parity.md`](https://github.com/Git-Uzair/chowki/blob/main/docs/research/07-cross-sdk-parity.md)
pins the byte-level algorithms — canonical JSON, args hashing, redaction placeholders,
token signing — and `spec/v1/vectors/` holds conformance fixtures the Node implementation
must reproduce byte-for-byte against the Python one.

Importing this package gives you three constants and a `createEngine()` that throws with a
pointer to the Python SDK, so a mistaken install fails loudly rather than silently doing
nothing.

Watch the repository for releases. MIT licensed.
