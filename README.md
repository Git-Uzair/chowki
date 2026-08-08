# chowki

`chowki` is an agent-native, in-process control plane and durable execution engine designed for Python and polyglot environments. It embeds directly into existing application codebases via lightweight decorators, providing zero-infrastructure warm resume, automated secret redaction, and active guardrails.

## Installation

```bash
uv add chowki
```

## Quickstart

<!-- kept in sync with examples/python/quickstart.py (Task 22) -->
```python
import chowki


@chowki.step
def process_data(item: str) -> str:
    return f"processed {item}"


@chowki.workflow
def my_workflow(items: list[str]) -> list[str]:
    results = []
    for item in items:
        results.append(process_data(item))
    return results


if __name__ == "__main__":
    result = chowki.resume("wf_123", fn=my_workflow, items=["a", "b"])
```
