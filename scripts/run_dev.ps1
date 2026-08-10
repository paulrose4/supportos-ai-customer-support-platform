$ErrorActionPreference = "Stop"
python -m uvicorn app.main:app --reload