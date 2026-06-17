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

## Database

The backend uses MySQL through SQLAlchemy and PyMySQL. Configure the local database in `.env`:

```text
DATABASE_HOST=127.0.0.1
DATABASE_PORT=3306
DATABASE_USER=root
DATABASE_PASSWORD=root
DATABASE_NAME=aijiu
```

Tables are created automatically when the FastAPI app starts.

Existing MySQL databases can add the AI type field manually:

```sql
ALTER TABLE ai_agents
ADD COLUMN type VARCHAR(30) NOT NULL DEFAULT 'business';
```

## Local AI API

Register a local AI service without an API key:

```json
POST /ais

{
  "name": "qwen2.5:7b",
  "api_url": "http://127.0.0.1:11434/v1/chat/completions",
  "type": "business",
  "initial_score": 1.0
}
```

Invoke the registered AI for an assigned task:

```text
POST /ais/{ai_id}/tasks/{task_id}/invoke
```

The `name` value is sent as the OpenAI `model` field. The backend sends this
OpenAI Chat Completions compatible JSON to `api_url`:

```json
{
  "model": "qwen2.5:7b",
  "messages": [
    {
      "role": "system",
      "content": "你是一名文化遗产领域的专业问答助手。"
    },
    {
      "role": "user",
      "content": "问题：Question text\n\n回答格式要求：bullet list\n\n请严格按照回答格式要求作答。"
    }
  ],
  "stream": false
}
```

The local service should return an OpenAI-compatible
`choices[0].message.content` response.

## Aggregator AI

Register an aggregator AI with `type: "aggregator"`:

```json
{
  "name": "qwen2.5:14b",
  "api_url": "http://127.0.0.1:11434/v1/chat/completions",
  "type": "aggregator",
  "initial_score": 1.0
}
```

Workers and business AI services continue to submit raw answers. The frontend
then completes aggregation and scoring with:

```text
POST /tasks/{task_id}/finish
```

Development-only endpoints are also available:

```text
POST /tasks/{task_id}/aggregate
POST /tasks/{task_id}/score
```

## AI Health Checks

When the application starts, all enabled AI services are checked in the
background. Startup is not blocked by slow or unavailable models.

Run an immediate check for one AI:

```text
POST /ais/{ai_id}/health-check
```

The response contains:

```json
{
  "available": true,
  "last_checked_at": "2026-06-09T12:00:00",
  "last_error": null
}
```

`GET /ais` also returns these fields for each AI, so the frontend can display
online/offline status without performing its own health check.

## CORS

Set allowed frontend origins with `BACKEND_CORS_ORIGINS` in `.env`.

```text
BACKEND_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:5173
```
