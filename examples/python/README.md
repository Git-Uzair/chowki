# Chowki Python Examples

This directory contains runnable python examples demonstrating `chowki` features.

## Examples

### Quickstart (`quickstart.py`)

A standalone, zero-dependency script demonstrating:
1. Workflow and step decoration (`@chowki.step`, `@chowki.workflow`)
2. Human-in-the-Loop workflow suspension (`chowki.pause`) with console notifications (`ConsoleGateway`)
3. Capturing `chowki.WorkflowPaused` and single-use resume tokens
4. Applying RFC 6902 JSON Patch state modifications during warm resume (`chowki.resume`)

Run with:

```bash
uv run python examples/python/quickstart.py
```

## Best Practices

- **Workflow Side Effects Rule**: Every side effect in a Chowki workflow must live inside a `@chowki.step`. Because `resume()` re-executes the workflow function body from the top, any side effect outside a `@chowki.step` will be re-executed on warm resume.
