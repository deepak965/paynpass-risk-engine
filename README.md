# PayNPass Risk Intelligence Engine

A deterministic, rule-based behavioral risk scoring service for the **PayNPass Scan & Pay** platform.  
Built with **Python FastAPI + PostgreSQL + SQLAlchemy**.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Risk Scoring Engine](#risk-scoring-engine)
4. [Running Locally](#running-locally)
5. [API Reference with Examples](#api-reference-with-examples)
6. [Database Schema](#database-schema)
7. [Security Model](#security-model)

---

## Architecture Overview

```
PayNPass Mobile App
       │
       ▼
Main Commerce Backend  ──── (posts events via internal API key)
       │
       ▼
┌─────────────────────────────────────────┐
│     Risk Intelligence Engine (this)     │
│                                         │
│  POST /events   →  Risk Engine          │
│                     │                   │
│                     ▼                   │
│               Rule Evaluator            │
│  R01 Long stay + low cart               │
│  R02 Excessive cart edits               │
│  R03 Repeated item removals             │
│  R04 Payment gap                        │
│  R05 Repeat offender                    │
│  R06 No checkout after long time        │
│  R07 Rapid scan→remove cycles           │
└──────────────┬──────────────────────────┘
               │
               ▼
        PostgreSQL Risk DB
               │
               ▼
    Vendor Analytics Dashboard
    Security Staff Tablet App
```

### Risk Score Levels

| Score | Level  | Colour | Action                        |
|-------|--------|--------|-------------------------------|
| 0–30  | LOW    | 🟢 Green  | No action needed           |
| 31–60 | MEDIUM | 🟡 Yellow | Optional spot-check        |
| 61–100| HIGH   | 🔴 Red    | Mandatory inspection       |

---

## Project Structure

```
paynpass-risk-engine/
├── app/
│   ├── main.py                        # FastAPI app factory
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py            # Router registration
│   │       └── endpoints/
│   │           ├── auth.py            # Login / register
│   │           ├── events.py          # POST /events
│   │           ├── sessions.py        # Session CRUD + risk
│   │           ├── inspections.py     # Security inspection
│   │           └── analytics.py       # Vendor dashboard APIs
│   ├── core/
│   │   ├── config.py                  # Settings (pydantic-settings)
│   │   └── security.py                # JWT + password + API key
│   ├── db/
│   │   └── database.py                # SQLAlchemy engine + session
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py                    # User, Store
│   │   ├── session.py                 # Session, Event
│   │   └── inspection.py              # Product, Inspection
│   ├── schemas/
│   │   ├── event.py                   # Pydantic I/O for events
│   │   ├── session.py                 # Pydantic I/O for sessions
│   │   └── inspection.py              # Pydantic I/O for inspections + auth
│   └── services/
│       ├── risk_engine.py             # ⭐ Core rule-based risk scorer
│       └── session_service.py         # Session business logic
├── scripts/
│   └── seed.py                        # Bootstrap test data
├── tests/
│   └── test_risk_engine.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── alembic.ini
└── .env.example
```

---

## Risk Scoring Engine

File: `app/services/risk_engine.py`

Each rule is a pure function returning a `RuleResult(rule_id, triggered, points, reason)`.

| Rule | ID  | Trigger Condition                                      | Points |
|------|-----|--------------------------------------------------------|--------|
| Long stay + low cart | R01 | In store > 25 min AND cart < ₹200           | +15    |
| Excessive edits      | R02 | Cart edited > 6 times                        | +10    |
| Repeated removals    | R03 | > 3 PRODUCT_REMOVE events                   | +10    |
| Payment gap          | R04 | Paid < 70% of cart value (cart ≥ ₹100)      | +20    |
| Repeat offender      | R05 | User has ≥ 1 prior MISMATCH inspection      | +20    |
| No checkout          | R06 | Items scanned, no checkout after > 30 min   | +15    |
| Rapid scan→remove    | R07 | ≥ 4 items removed within 60s of scanning   | +10    |

All thresholds are defined in a single `THRESHOLDS` dict at the top of the file — easy to tune without touching rule logic.

---

## Running Locally

### Option A – Docker Compose (recommended)

```bash
# 1. Clone and enter project
git clone <repo>
cd paynpass-risk-engine

# 2. Configure environment
cp .env.example .env
# Edit SECRET_KEY and INTERNAL_API_KEY in .env

# 3. Start everything
docker compose up --build

# 4. Seed test data
docker compose exec api python scripts/seed.py

# 5. Open API docs
open http://localhost:8000/docs
```

### Option B – Local Python

```bash
# 1. Create virtualenv
python -m venv venv && source venv/bin/activate

# 2. Install deps
pip install -r requirements.txt

# 3. Start PostgreSQL and create DB
createdb paynpass_risk

# 4. Configure .env
cp .env.example .env
# Set DATABASE_URL to your local postgres URL

# 5. Run the server
uvicorn app.main:app --reload

# 6. Seed test data
python scripts/seed.py
```

---

## API Reference with Examples

Base URL: `http://localhost:8000/api/v1`

---

### Auth

#### POST /auth/login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "vendor1@paynpass.in",
    "password": "Vendor@1234"
  }'
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

---

### Events (Server-to-Server)

All event calls require header: `X-Internal-API-Key: <your_key>`

#### SESSION_START

```bash
curl -X POST http://localhost:8000/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: CHANGE_ME_INTERNAL" \
  -d '{
    "event_ref": "evt-001",
    "session_ref": "sess-abc-123",
    "user_id": 4,
    "store_id": 1,
    "event_type": "SESSION_START",
    "cart_value": 0,
    "timestamp": "2024-01-15T10:00:00Z"
  }'
```

**Response:**
```json
{
  "id": 1,
  "event_ref": "evt-001",
  "session_id": 1,
  "event_type": "SESSION_START",
  "timestamp": "2024-01-15T10:00:00Z",
  "risk_score": 0,
  "risk_level": "LOW",
  "flagged_for_inspection": false
}
```

#### PRODUCT_SCAN

```bash
curl -X POST http://localhost:8000/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: CHANGE_ME_INTERNAL" \
  -d '{
    "event_ref": "evt-002",
    "session_ref": "sess-abc-123",
    "user_id": 4,
    "store_id": 1,
    "event_type": "PRODUCT_SCAN",
    "product_id": 1,
    "quantity": 1,
    "price": 72.00,
    "cart_value": 72.00,
    "timestamp": "2024-01-15T10:05:00Z"
  }'
```

#### PRODUCT_REMOVE

```bash
curl -X POST http://localhost:8000/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: CHANGE_ME_INTERNAL" \
  -d '{
    "event_ref": "evt-003",
    "session_ref": "sess-abc-123",
    "user_id": 4,
    "store_id": 1,
    "event_type": "PRODUCT_REMOVE",
    "product_id": 1,
    "quantity": 1,
    "cart_value": 0.00,
    "timestamp": "2024-01-15T10:05:45Z"
  }'
```

#### PAYMENT_SUCCESS

```bash
curl -X POST http://localhost:8000/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: CHANGE_ME_INTERNAL" \
  -d '{
    "event_ref": "evt-010",
    "session_ref": "sess-abc-123",
    "user_id": 4,
    "store_id": 1,
    "event_type": "PAYMENT_SUCCESS",
    "cart_value": 45.00,
    "timestamp": "2024-01-15T10:35:00Z"
  }'
```

---

### Sessions

#### GET /session/{id}

```bash
curl http://localhost:8000/api/v1/session/1 \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "id": 1,
  "session_ref": "sess-abc-123",
  "user_id": 4,
  "store_id": 1,
  "start_time": "2024-01-15T10:00:00Z",
  "end_time": null,
  "total_items_scanned": 3,
  "total_items_removed": 4,
  "cart_edit_count": 9,
  "cart_value": "45.00",
  "payment_amount": "45.00",
  "time_spent_minutes": 35.0,
  "risk_score": 55,
  "risk_level": "MEDIUM",
  "flagged_for_inspection": false,
  "status": "ACTIVE"
}
```

#### GET /session/{id}/risk

```bash
curl http://localhost:8000/api/v1/session/1/risk \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "session_id": 1,
  "score": 55,
  "level": "MEDIUM",
  "flagged": false,
  "rules_triggered": [
    {
      "rule_id": "R01",
      "points": 15,
      "reason": "In store 35.0 min with cart ₹45.00 (threshold: >25 min & <₹200.00)"
    },
    {
      "rule_id": "R02",
      "points": 10,
      "reason": "Cart edited 9 times (threshold: >6)"
    },
    {
      "rule_id": "R03",
      "points": 10,
      "reason": "4 item removals (threshold: >3)"
    },
    {
      "rule_id": "R07",
      "points": 10,
      "reason": "4 rapid scan→remove cycle(s) within 60s (threshold: ≥4)"
    }
  ],
  "rules_evaluated": 7
}
```

---

### Inspection

#### POST /inspection

```bash
curl -X POST http://localhost:8000/api/v1/inspection \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <security_token>" \
  -d '{
    "session_ref": "sess-abc-123",
    "store_id": 1,
    "security_user_id": 3,
    "inspection_result": "MISMATCH",
    "mismatch_value": 144.00,
    "notes": "Customer had 2 Maggi packs in bag not in cart",
    "timestamp": "2024-01-15T10:42:00Z"
  }'
```

**Response:**
```json
{
  "id": 1,
  "session_id": 1,
  "store_id": 1,
  "security_user_id": 3,
  "inspection_result": "MISMATCH",
  "mismatch_value": "144.00",
  "notes": "Customer had 2 Maggi packs in bag not in cart",
  "timestamp": "2024-01-15T10:42:00Z"
}
```

---

### Analytics

All analytics require `Authorization: Bearer <vendor_or_admin_token>`.

#### GET /analytics/store-summary

```bash
curl "http://localhost:8000/api/v1/analytics/store-summary" \
  -H "Authorization: Bearer <vendor_token>"
```

**Response:**
```json
{
  "store_id": 1,
  "date": "2024-01-15",
  "total_sessions": 42,
  "active_sessions": 8,
  "completed_sessions": 34,
  "high_risk_sessions": 5,
  "medium_risk_sessions": 12,
  "low_risk_sessions": 25,
  "flagged_sessions": 5,
  "inspections_performed": 7,
  "mismatch_count": 2,
  "total_mismatch_value": "376.00",
  "average_cart_value": "312.50",
  "average_risk_score": 22.4
}
```

#### GET /analytics/risk-distribution

```bash
curl "http://localhost:8000/api/v1/analytics/risk-distribution" \
  -H "Authorization: Bearer <vendor_token>"
```

**Response:**
```json
[
  { "risk_level": "LOW",    "count": 25, "percentage": 59.5 },
  { "risk_level": "MEDIUM", "count": 12, "percentage": 28.6 },
  { "risk_level": "HIGH",   "count": 5,  "percentage": 11.9 }
]
```

#### GET /analytics/high-risk-sessions

```bash
curl "http://localhost:8000/api/v1/analytics/high-risk-sessions?limit=10" \
  -H "Authorization: Bearer <vendor_token>"
```

#### GET /analytics/inspection-results?result_filter=MISMATCH

```bash
curl "http://localhost:8000/api/v1/analytics/inspection-results?result_filter=MISMATCH" \
  -H "Authorization: Bearer <vendor_token>"
```

---

## Database Schema

```sql
-- Users (customers, vendors, security, admins)
CREATE TABLE users (
    id               SERIAL PRIMARY KEY,
    name             VARCHAR(120) NOT NULL,
    email            VARCHAR(255) UNIQUE NOT NULL,
    phone            VARCHAR(20),
    hashed_password  VARCHAR(255) NOT NULL,
    role             user_role NOT NULL DEFAULT 'customer',
    store_id         INTEGER REFERENCES stores(id),
    is_active        BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ
);

-- Stores (multi-store architecture)
CREATE TABLE stores (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(200) NOT NULL,
    address    VARCHAR(500),
    city       VARCHAR(100),
    state      VARCHAR(100),
    pincode    VARCHAR(10),
    gstin      VARCHAR(20),
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ
);

-- Shopping sessions
CREATE TABLE sessions (
    id                      SERIAL PRIMARY KEY,
    session_ref             VARCHAR(64) UNIQUE NOT NULL,
    user_id                 INTEGER REFERENCES users(id) NOT NULL,
    store_id                INTEGER REFERENCES stores(id) NOT NULL,
    start_time              TIMESTAMPTZ NOT NULL,
    end_time                TIMESTAMPTZ,
    total_items_scanned     INTEGER DEFAULT 0,
    total_items_removed     INTEGER DEFAULT 0,
    cart_edit_count         INTEGER DEFAULT 0,
    cart_value              NUMERIC(10,2) DEFAULT 0,
    payment_amount          NUMERIC(10,2),
    time_spent_minutes      FLOAT,
    risk_score              INTEGER DEFAULT 0,
    risk_level              risk_level DEFAULT 'LOW',
    flagged_for_inspection  BOOLEAN DEFAULT FALSE,
    status                  session_status DEFAULT 'ACTIVE',
    created_at              TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ
);

-- Behavioral events (high-throughput)
CREATE TABLE events (
    id             SERIAL PRIMARY KEY,
    event_ref      VARCHAR(64) UNIQUE NOT NULL,   -- idempotency key
    session_id     INTEGER REFERENCES sessions(id) NOT NULL,
    user_id        INTEGER REFERENCES users(id) NOT NULL,
    store_id       INTEGER REFERENCES stores(id) NOT NULL,
    event_type     event_type NOT NULL,
    product_id     INTEGER REFERENCES products(id),
    quantity       INTEGER,
    price          NUMERIC(10,2),
    cart_value     NUMERIC(10,2),
    metadata_json  TEXT,
    timestamp      TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ
);

-- Products (per-store catalogue)
CREATE TABLE products (
    id         SERIAL PRIMARY KEY,
    store_id   INTEGER REFERENCES stores(id) NOT NULL,
    barcode    VARCHAR(64),
    name       VARCHAR(255) NOT NULL,
    category   VARCHAR(100),
    price      NUMERIC(10,2) NOT NULL,
    mrp        NUMERIC(10,2),
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ
);

-- Security inspection results
CREATE TABLE inspections (
    id                  SERIAL PRIMARY KEY,
    session_id          INTEGER REFERENCES sessions(id) UNIQUE NOT NULL,
    store_id            INTEGER REFERENCES stores(id) NOT NULL,
    security_user_id    INTEGER REFERENCES users(id) NOT NULL,
    inspection_result   inspection_result NOT NULL,
    mismatch_value      NUMERIC(10,2),
    notes               TEXT,
    timestamp           TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ
);
```

---

## Security Model

| Endpoint group          | Auth method         | Notes                                  |
|-------------------------|---------------------|----------------------------------------|
| `POST /events`          | `X-Internal-API-Key`| Server-to-server only                  |
| `POST /session/start`   | `X-Internal-API-Key`| Server-to-server only                  |
| `POST /session/end`     | `X-Internal-API-Key`| Server-to-server only                  |
| `GET /session/{id}`     | JWT Bearer          | Vendor/security: own store only        |
| `GET /session/{id}/risk`| JWT Bearer          | Vendor/security: own store only        |
| `POST /inspection`      | JWT Bearer          | `security` or `admin` role required    |
| `GET /analytics/*`      | JWT Bearer          | Vendor: own store. Admin: any store    |
| `POST /auth/register`   | JWT Bearer          | `admin` role required                  |

All JWT tokens carry `sub` (user ID) and `role` claims.  
Multi-store isolation is enforced at the service layer — vendors and security staff are bound to `user.store_id`.
