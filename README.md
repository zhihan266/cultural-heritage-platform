# Cultural Heritage QA Backend

FastAPI backend prototype for cultural heritage question answering with users, workers, and AI agents.

## Project Structure

```text
app/          Business code and FastAPI entrypoint
config/       Runtime settings and logging configuration
tests/        HTTP examples and future automated tests
requirements.txt
```

## Run

```powershell
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open the API docs at http://127.0.0.1:8000/docs.
