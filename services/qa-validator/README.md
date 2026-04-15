# MIP QA Validation Service

Standalone menu capture QA validator. Reads from source schemas, 
writes only to qa.*. Deployed as independent Railway service.

## Endpoints
POST   /qa/runs                 — trigger sample or full run
GET    /qa/runs/{id}/status     — run status + checkpoint
GET    /qa/reports/latest       — latest daily report
GET    /qa/reports/{date}       — report by date (YYYY-MM-DD)
GET    /health                  — liveness

## Auth
All endpoints except /health require: X-Admin-Key: <ADMIN_API_KEY>

## Env vars
See .env.example
