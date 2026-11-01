---
name: fastapi
when: [api]
keywords: [fastapi, endpoint, route, api]
---
# FastAPI
- Request/response bodies are Pydantic models; return the model, not a dict, when shape matters.
- Validation errors come free as 422; raise `HTTPException(status_code, detail)` for the rest.
- Keep handlers thin: call a plain function that holds the logic and is tested directly.
- Blocking work (subprocess, disk) goes through `run_in_threadpool` in async handlers.
- Test with `fastapi.testclient.TestClient(app)`; assert status code and body.
```python
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```
