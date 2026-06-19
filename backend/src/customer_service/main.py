from fastapi import FastAPI

app = FastAPI(title="Smart Customer Service API")


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
