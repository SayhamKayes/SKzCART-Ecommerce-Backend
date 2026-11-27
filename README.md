<div align="center">

# ⚙️ SKzCART | Backend API Engine
### **Django REST Core, Neon PostgreSQL Persistence, GA4 & Meta CAPI Telemetry, Payment Gateways & Courier Hub**

[![Django](https://img.shields.io/badge/Django_5.2-092E20?style=for-the-badge&logo=django&logoColor=white)](https://www.djangoproject.com/)
[![Django REST Framework](https://img.shields.io/badge/DRF-A30000?style=for-the-badge&logo=django&logoColor=white)](https://www.django-rest-framework.org/)
[![PostgreSQL](https://img.shields.io/badge/Neon_PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white)](https://neon.tech/)
[![Cloudinary](https://img.shields.io/badge/Cloudinary-3448C5?style=for-the-badge&logo=cloudinary&logoColor=white)](https://cloudinary.com/)
[![Render Ready](https://img.shields.io/badge/Render-Gunicorn_WSGI-46E3B7?style=for-the-badge&logo=render&logoColor=black)](https://render.com/)

<p align="center">
  <b>Modular Django REST Framework backend powering SKzCART.</b><br/>
  Configured with the core Django package <code>shop</code>, central <b>Neon Serverless PostgreSQL</b> persistence, dynamic store-branded root status, atomic checkout with dynamic delivery zone fees, fraud scoring, SSLCommerz &amp; MFS gateway verification, Bangladesh courier integrations (Pathao, Steadfast, RedX, eCourier with Testing Sandbox &amp; Live Mode), GA4 visitor/funnel telemetry, encrypted Meta Ads / CAPI relays, strict SMTP email validation, HMAC staff tokens with TOTP/Email 2FA, and a central database Recycle Bin.
</p>

</div>

---

## 🏗️ Backend Architecture

**SKzCART Backend** follows a strict modular Django app architecture routed centrally under `/api/v1/` (`api.urls`), connected to **Neon PostgreSQL** via `DATABASE_URL` with PgBouncer pooler optimizations (`conn_max_age=60`, `conn_health_checks=True`, SSL enforcement, `DISABLE_SERVER_SIDE_CURSORS=True`).

The root backend landing page (`GET /`) and status endpoints (`GET /api/v1/`, `GET /api/v1/health`) dynamically resolve the store name from `ShopSetting` (defaulting cleanly to `E-Commerce Backend`), live storefront links (`FRONTEND_URL` / `APP_URL` / reverse-proxy host resolution), active Django/Python versions, and live endpoint URLs.

### 1. Modular System & Integration Architecture

```mermaid
graph TB
    Client["🖥️ Frontend SPA (Vercel / Vite Port 3001)"] -->|HTTPS REST JSON| WSGI["🌐 Gunicorn WSGI + WhiteNoise<br/>(shop/wsgi.py & shop/settings.py)"]
    WSGI --> Router["🔀 Centralized API Router<br/>(/api/v1/* via api/urls.py)"]

    subgraph Django_Apps["🐍 SKzCART Domain Apps (backend/)"]
        Core["⚙️ core<br/>ShopSetting, StaffMember (Auth, 2FA, OTP, Portal Access),<br/>AuditLog, RecycleBinItem, GA4 AnalyticsVisit,<br/>MarketingConfig/Event/UTM, MetaTenantConnection"]
        Products["📦 products<br/>Product, Coupon, Review, MediaAsset<br/>Bulk CSV &amp; Image Management"]
        Order["🛒 order<br/>Atomic Checkout, Stock Deduction, Fraud Scoring,<br/>Dynamic Delivery Zones &amp; PDF Statement Export"]
        Couriers["🚚 couriers<br/>CourierConfig (Sandbox Testing &amp; Live Mode)<br/>&amp; Official Webhook Ingress"]
        Payments["💳 payments<br/>PaymentTransaction, PaymentGatewayConfig,<br/>PaymentMethodSetting &amp; SSLCommerz Lifecycle"]
        Users["👥 users<br/>CustomerProfile Auth, Orders &amp; Password Reset"]
    end

    subgraph External_Infrastructure["🌐 Cloud Database & External Providers"]
        Neon[("🐘 Neon PostgreSQL<br/>(Central Cloud DB via DATABASE_URL)")]
        Cloudinary["☁️ Cloudinary Media CDN"]
        SSLCommerz["💳 SSLCommerz Gateway API"]
        SMTP["📧 SMTP Email Server<br/>(Orders, Lead Follow-ups, OTPs)"]
        Logistics["🚚 Pathao / Steadfast / RedX / eCourier"]
        MetaCAPI["📊 Meta Conversions API (CAPI) &amp; Graph Ads"]
        GA4["📈 Google Analytics 4 (Measurement Protocol v2)"]
    end

    Router --> Core
    Router --> Products
    Router --> Order
    Router --> Couriers
    Router --> Payments
    Router --> Users

    Core --> Neon
    Products --> Neon
    Order --> Neon
    Couriers --> Neon
    Payments --> Neon
    Users --> Neon

    Products --> Cloudinary
    Payments --> SSLCommerz
    Core --> SMTP
    Order --> SMTP
    Core --> MetaCAPI
    Core --> GA4
    Couriers --> Logistics
```

---

### 2. Atomic Checkout, Anti-Fraud Scoring & Notification Workflow

```mermaid
sequenceDiagram
    autonumber
    participant Client as 🖥️ Checkout UI
    participant OrderView as ⚙️ order.views.checkout_view
    participant DB as 🗄️ Neon PostgreSQL
    participant Notif as 📧 Email SMTP Service
    participant Audit as 🛡️ core.AuditLog

    Client->>OrderView: POST /api/v1/checkout { items, address, paymentMethod, couponCode }
    OrderView->>OrderView: Validate BD phone & resolve dynamic district/free-shipping fee
    OrderView->>OrderView: Evaluate Anti-Fraud Risk Score (0–100)
    critical Atomic Transaction
        OrderView->>DB: Lock Product rows (select_for_update) & deduct stock
        OrderView->>DB: Persist Order with custom Order ID prefix & statusHistory
    end
    OrderView->>Notif: Dispatch order confirmation email (if live SMTP enabled)
    OrderView->>Audit: Record ORDER_CREATED with IP address & details
    OrderView-->>Client: 201 Created { orderId, trackingToken, order }
```

---

### 3. Staff Authentication, Two-Step Verification (2FA) & Password Recovery

```mermaid
sequenceDiagram
    autonumber
    actor Staff as 👨‍💼 Staff / Admin
    participant AuthUI as 🔐 AdminLoginModal
    participant StaffAPI as ⚙️ core.views (staff_login_view / verify_2fa)
    participant SecurityDB as 🗄️ ShopSetting.extraConfig['security']
    participant SMTP as 📧 Configured SMTP

    Staff->>AuthUI: Enters username & password
    AuthUI->>StaffAPI: POST /api/v1/staff-auth/login
    StaffAPI->>SecurityDB: Check StaffMember status & 2FA policy (ALL_STAFF / ADMIN_ONLY)
    alt 2FA Disabled
        StaffAPI-->>AuthUI: 200 OK { token (HMAC 7d), user }
    else 2FA Enabled (TOTP or EMAIL_OTP)
        opt method == EMAIL_OTP
            StaffAPI->>SMTP: Send 6-digit OTP code
        end
        StaffAPI-->>AuthUI: 200 OK { requiresTwoStep: true, challengeToken, method }
        Staff->>AuthUI: Enters 6-digit TOTP / Email OTP / Backup PIN
        AuthUI->>StaffAPI: POST /api/v1/staff-auth/verify-2fa { challengeToken, code }
        StaffAPI->>SecurityDB: Verify TOTP (±30s window) / OTP Hash / Recovery PIN
        StaffAPI-->>AuthUI: 200 OK { token, user } + AuditLog event
    end
```

---

### 4. Courier Assignment, Testing/Live Mode & Webhook Ingress

```mermaid
sequenceDiagram
    autonumber
    actor Dispatcher as 👨‍💼 Admin / Dispatcher
    participant CourierView as ⚙️ couriers.views
    participant Hub as 🚚 Pathao / Steadfast / RedX / eCourier API
    participant DB as 🗄️ Neon PostgreSQL

    Dispatcher->>CourierView: POST /api/v1/couriers/assign { orderId, provider }
    CourierView->>Hub: Create Parcel Consignment (or Sandbox Simulator when Testing Mode enabled)
    Hub-->>CourierView: Consignment ID & Tracking Code
    CourierView->>DB: Update Order (COURIER_CREATED) & timeline
    Hub->>CourierView: POST /api/v1/webhooks/couriers/<provider> { status, tracking_id }
    CourierView->>DB: Sync Order status (PICKED_UP / IN_TRANSIT / DELIVERED / CANCELLED) + AuditLog
```

---

## 🗄️ Core Data Models (PostgreSQL ORM)

| App | Model | Key Fields & Responsibility |
|---|---|---|
| `core` | `ShopSetting` | Store identity, branding URLs, SMTP config, `extraConfig` JSON (header, footer, themesColors, campaign, `deliveryConfig` zones & free shipping thresholds, `googleAnalytics`, `portalAccess`, security/2FA, `idPrefixes`) |
| `core` | `StaffMember` | Role-based staff accounts (`ADMIN`, `MANAGER`, `DISPATCHER`, `RIDER`), `ACTIVE`/`SUSPENDED` status |
| `core` | `AuditLog` | Immutable security & operational trail with IP, actor, action, target |
| `core` | `RecycleBinItem` | Central soft-delete trash supporting single/bulk restore & permanent purge |
| `core` | `MarketingConfig`, `MarketingEvent` & `MarketingUtmCampaign` | Meta Pixel, CAPI token, GA4 ID, server-side conversion logs & DB-persisted UTM campaigns |
| `core` | `AnalyticsVisit` | Live storefront GA4 telemetry (`visitorId`, `sessionId`, `eventType`, `pagePath`, `trafficSource`, `deviceType`, `city`, `isNewVisitor`, `durationSeconds`, `productName`) |
| `core` | `StoreTenant`, `MetaTenantConnection`, `SaaSAdCampaign`, `SaaSAdSet`, `SaaSAdCreativeAd` | Multi-tenant SaaS Meta OAuth, Fernet-encrypted token storage, Ad Account health & two-way campaign sync |
| `core` | `NotificationLog` | Email dispatch records (`SENT`/`FAILED`, recipient, template, SMTP provider) |
| `products` | `Product`, `Coupon`, `Review`, `MediaAsset` | Product catalog (`360°` frames/video, variants, stock), discount coupons, reviews & Cloudinary assets |
| `order` | `Order` | Order totals, customer address JSON, fraud `riskScore`, `statusHistory`, courier & payment tracking |
| `couriers` | `CourierConfig` | Active toggle, Sandbox Testing/Live Mode flag, API keys, secrets & webhook tokens per provider |
| `payments` | `PaymentTransaction`, `PaymentGatewayConfig`, `PaymentMethodSetting` | Payment channel setup, SSLCommerz gateway credentials/mode, transaction logs & reconciliation |
| `users` | `CustomerProfile` | Customer accounts, saved BD addresses & hashed passwords |

---

## 📡 API Reference (`/api/v1/`)

### Root, System Health, Settings, Staff, Security & Portal Access (`core`, `api`)
- `GET /`, `GET /api/v1/`, `GET /api/v1/health` — Dynamic store-branded backend status, live storefront URL & endpoint registry
- `GET /api/v1/system/blueprint`, `POST /api/v1/system/migrate` — Live system readiness diagnostics & database migration trigger
- `GET, POST /api/v1/settings/shop` — Fetch or update global shop identity, theme, header, footer, delivery zones, prefixes & SMTP
- `POST /api/v1/settings/email/test` — Dispatch live SMTP verification test email
- `GET, POST /api/v1/notifications` — List email logs or dispatch strict SMTP-validated manual / Lead follow-up emails
- `GET /api/v1/staff`, `POST /api/v1/staff`, `GET, PUT, PATCH, DELETE /api/v1/staff/<staff_id>` — Staff management & soft-delete
- `POST /api/v1/staff-auth/{login,verify-2fa,forgot-password,reset-password,bootstrap}`, `GET /api/v1/staff-auth/{setup-status,verify}`
- `GET, POST /api/v1/security/2fa`, `GET, POST /api/v1/security/portal-access` — Two-Step Verification & portal access rules
- `GET /api/v1/courier-auth/riders`, `POST /api/v1/courier-auth/login` — Dynamic Courier Rider portal directory & PIN/staff login
- `GET /api/v1/audit-logs`, `DELETE /api/v1/audit-logs/<pk>`, `POST /api/v1/audit-logs/bulk-delete`
- `GET, POST /api/v1/recycle-bin`, `POST /api/v1/recycle-bin/<pk>/restore`, `DELETE /api/v1/recycle-bin/<pk>`, `POST /api/v1/recycle-bin/bulk-restore`, `POST /api/v1/recycle-bin/bulk-delete`

### Google Analytics 4 (GA4), Meta Pixel, CAPI & Ads (`core`)
- `GET, POST, PUT /api/v1/analytics/ga4/config` — Save & sync GA4 Measurement ID (`G-XXXX`), Property ID, Measurement Protocol Secret & active toggle
- `POST /api/v1/analytics/ga4/collect` — Storefront telemetry ingestion (`page_view`, `view_item`, `add_to_cart`, `begin_checkout`, `purchase`, `session_ping`) + optional Measurement Protocol v2 relay
- `POST /api/v1/analytics/ga4/test` — Validate GA4 Measurement ID & test Measurement Protocol debug relay
- `GET /api/v1/analytics/ga4/dashboard` — Multi-range (`7d`, `14d`, `30d`, `6m`, `1y`, `custom`, `all`) traffic sources, new vs returning, devices, cities, funnel drop-off %, top pages & recent activity with dynamic product resolution
- `GET, POST /api/v1/marketing/config`, `GET, POST, DELETE /api/v1/marketing/events`, `POST /api/v1/marketing/capi/event`, `POST /api/v1/marketing/sync-meta-ads`, `GET /api/v1/marketing/facebook-catalog.xml`
- `GET, POST /api/v1/marketing/utm-campaigns`, `PATCH, DELETE /api/v1/marketing/utm-campaigns/<pk>`
- `GET, POST /api/v1/marketing/tenants`, `/api/v1/marketing/meta/{connection,oauth/url,oauth/exchange,connect-token,select-assets,account-health,page-posts,campaigns,sync}`

### Catalog, Media, Coupons & Reviews (`products`)
- `GET, POST /api/v1/products`, `GET, PUT, PATCH, DELETE /api/v1/products/<pk>`
- `POST /api/v1/products/bulk-upload-csv`, `POST /api/v1/products/bulk-delete`, `POST /api/v1/products/bulk-stock`, `POST /api/v1/products/reorder`
- `POST /api/v1/upload/image`, `POST /api/v1/upload/image/delete`, `GET, POST /api/v1/media`, `POST /api/v1/media/sync`, `GET, PUT, DELETE /api/v1/media/<pk>`
- `GET, POST /api/v1/coupons`, `GET, PUT, DELETE /api/v1/coupons/<code>`
- `GET, POST /api/v1/reviews`, `GET, PUT, DELETE /api/v1/reviews/<pk>`

### Checkout, Orders & Tracking (`order`)
- `POST /api/v1/checkout` — Create order & atomically deduct stock
- `GET /api/v1/orders`, `GET, PATCH, DELETE /api/v1/orders/<pk>`, `PATCH /api/v1/orders/<pk>/status`, `POST /api/v1/orders/bulk-delete`
- `GET /api/v1/orders/statement/pdf` — Download filtered order statements (weekly, 15-day, monthly, custom range)
- `POST /api/v1/tracking/lookup` — Public order tracking by Order ID & phone

### Couriers, Payments & Customers (`couriers`, `payments`, `users`)
- `GET /api/v1/couriers`, `PUT /api/v1/couriers/<pk>`, `POST /api/v1/couriers/assign`, `GET /api/v1/couriers/<provider>/balance`, `GET /api/v1/couriers/<provider>/status/<tracking_code>`, `GET /api/v1/couriers/status`
- `POST /api/v1/webhooks/couriers`, `POST /api/v1/webhooks/couriers/<provider>`, `POST /api/v1/couriers/simulate-webhook`
- `GET, POST /api/v1/payments/methods`, `GET, POST /api/v1/payments/config`, `POST /api/v1/payments/initiate`, `POST /api/v1/payments/verify`
- `POST /api/v1/payments/sslcommerz/{success|fail|cancel|ipn}`, `GET, POST /api/v1/payments/sslcommerz/sandbox-simulator`
- `GET /api/v1/payments/transactions`, `DELETE /api/v1/payments/transactions/<pk>`, `POST /api/v1/payments/transactions/bulk-delete`
- `POST /api/v1/users/auth`, `POST /api/v1/users/forgot-password`, `GET, PUT, PATCH /api/v1/users/<phone>`, `GET /api/v1/users/<phone>/orders`

---

## 💻 Backend Tech Stack

| Package / Tool | Version / Spec | Purpose |
|---|---|---|
| **Django** | `>=5.0,<6.0` (`5.2`) | Core web framework, ORM, migrations & admin |
| **Django REST Framework** | `>=3.15.0` | JSON API serialization & viewsets |
| **Neon PostgreSQL** | PostgreSQL (Cloud) | Central relational + JSONB persistence (`DATABASE_URL`) |
| **`psycopg2-binary` & `dj-database-url`** | `>=2.9.9` / `>=2.1.0` | PostgreSQL driver & pooled SSL DSN parser |
| **`cryptography`** | `>=42.0.0` | Fernet encryption for stored Meta OAuth/System tokens |
| **`django-cors-headers`** | `>=4.3.0` | Cross-origin requests from Vercel/custom frontend domains |
| **`whitenoise`** | `>=6.6.0` | Production static file serving without external CDN |
| **`gunicorn`** | `>=21.2.0` | Production WSGI HTTP server |
| **`cloudinary` & `Pillow`** | `>=1.40.0` / `>=10.0.0` | Cloud image/video hosting & media processing |
| **`requests` & `python-dotenv`** | `>=2.31.0` / `>=1.0.0` | Courier/Payment/Meta/GA4 HTTP calls & `.env` loading |

---

## 📁 Backend Directory Structure

```text
backend/
├── manage.py                     # Django CLI entrypoint (DJANGO_SETTINGS_MODULE=shop.settings)
├── requirements.txt              # Python production dependencies
├── build.sh                      # Render build script (pip install, collectstatic, migrate, seed_data)
├── passenger_wsgi.py             # Alternate cPanel Passenger WSGI entrypoint
├── .env.example                  # Backend environment variable template
├── .gitignore                    # Ignores .env*, .runtime_secret_key, db.sqlite3, *.sqlite3, initial_data.json, shop_env/, media/
├── shop/                         # Core Django project package
│   ├── settings.py               # Neon PostgreSQL DATABASE_URL, CORS, WhiteNoise, SSLCommerz, SMTP
│   ├── urls.py                   # Root URLs (/, /admin/, /api/v1/, /media/)
│   ├── wsgi.py                   # WSGI boot + AUTO_MIGRATE_ON_BOOT / AUTO_SEED_ON_BOOT hooks
│   └── asgi.py                   # ASGI entrypoint
├── api/                          # Centralized /api/v1/ router, system blueprint & migrate probe
├── core/                         # ShopSetting, StaffMember, AuditLog, 2FA, RecycleBin, Marketing/CAPI,
│                                 # analytics_views.py (GA4), meta_ads_views.py, meta_ads_service.py, meta_security.py
├── products/                     # Catalog, Coupons, Reviews & Cloudinary Media Library
├── order/                        # Checkout, Anti-Fraud Scoring, Statements & Strict SMTP Email Notifications
├── couriers/                     # Pathao, Steadfast, RedX & eCourier API adapters + Sandbox/Live webhooks
├── payments/                     # SSLCommerz & payment gateway/method/transaction management
└── users/                        # Customer authentication & password recovery
```

---

## 🚀 Local Setup & Render Deployment Guide

### Local Development
```powershell
# 1. Create & activate virtual environment
python -m venv shop_env
.\shop_env\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure .env with your Neon DATABASE_URL & DJANGO_SECRET_KEY, then migrate
python manage.py migrate
python manage.py check

# 4. Run development server
python manage.py runserver 127.0.0.1:8000
```

### Render Deployment (Recommended for Backend)
1. Push the `backend/` folder to its own GitHub repository.
2. Create a new **Web Service** on **Render**:
   - **Runtime:** `Python 3`
   - **Build Command:** `./build.sh`
   - **Start Command:** `gunicorn shop.wsgi:application`
3. Add **Environment Variables** on Render:
   - `DATABASE_URL` = Your Neon PostgreSQL pooled connection string (`?sslmode=require`)
   - `DJANGO_SECRET_KEY` = Strong 50+ char random secret key
   - `DEBUG` = `False`
   - `ALLOWED_HOSTS` = `*` (or `your-service.onrender.com`)
   - `CSRF_TRUSTED_ORIGINS` = `https://your-vercel-frontend.vercel.app,https://your-service.onrender.com`
   - `AUTO_MIGRATE_ON_BOOT` = `True`, `AUTO_SEED_ON_BOOT` = `False`
   - `APP_URL` & `FRONTEND_URL` = `https://your-vercel-frontend.vercel.app`
   - `BACKEND_URL` = `https://your-service.onrender.com`
   - `SSLCOMMERZ_FRONTEND_URL` = `https://your-vercel-frontend.vercel.app`
   - `SSLCOMMERZ_BACKEND_URL` = `https://your-service.onrender.com`
   - `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, `CLOUDINARY_FOLDER`
   - `PYTHON_VERSION` = `3.12.8`
