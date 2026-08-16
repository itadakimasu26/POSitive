# OXPOS — Django Edition

OXPOS is a responsive point-of-sale web application built with Python and Django. It combines checkout, inventory, reporting, staff controls, and multi-store operations in one cloud-based workspace.

## Included features

- Platform-superuser administration and issued store logins
- Temporary-password verification with required first-login password replacement
- Registered-email password recovery with expiring reset links
- Multiple isolated businesses with per-user store assignments and switching
- Self-service registration that provisions one 30-day Trial store automatically
- Live dashboard totals and seven-day sales chart
- Interactive sales terminal with Cash, GCash, and Card payments
- Cafe-only dine-in and take-out checkout with configurable dine-in service charges captured on receipts and reports; other business categories use a standard retail sale
- Downloadable offline-sales kit with atomic, duplicate-safe recovery import after an outage
- Atomic stock validation, duplicate-cart protection, and automatic inventory deduction
- Database-backed receipts and sale line items
- Historical unit-cost snapshots for stable profit reporting
- Product creation, search, barcode generation, smart temporary pictures, editable hosted product photos, CSV export, and stock adjustment
- Revenue, profit, payment-mix, and top-product reports
- Store-administrator/staff access controls, store details, tax rate, and sales preferences
- Starter at ₱399/month: 1 store administrator, 2 cashiers, and the essential POS workflow
- Pro at ₱799/month: shared administration across Pro stores, up to 3 administrators and 10 role-based staff per store, and advanced operations
- Pro owner super dashboard with portfolio-wide daily and 30-day sales, inventory health, teams, customers, subscription timing, register state, purchase orders, alerts, and one-click store access
- Pro discounts with manager approval, refunds/voids, customer profiles, and loyalty points
- Pro stocktakes, inventory movement history, inter-store transfers, and bulk product import
- Pro shared daily cash register with staff handovers, logout custody tracking, reconciliation, and audit CSV export
- Platform-managed plans, published prices, subscription dates, and expiry monitoring
- Expired-store extension requests with plan, payment preference, comments, platform-admin tracking, and OXPOS email notification
- Public feature/plan comparison and downloadable PDF guide
- Persistent light and dark themes for store workspaces
- Django administration area
- Automated tests for checkout, stock, authentication, subscriptions, and exports

## Run in VS Code on Windows

### 1. Install Python

Install Python 3.11 or newer and enable **Add Python to PATH** during installation.

### 2. Open the project

Extract the ZIP, open the `OXPOS-Django` folder in VS Code, then open **Terminal → New Terminal**.

### 3. Create and activate a virtual environment

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run this once in the same terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

### 4. Install Django

```powershell
python -m pip install -r requirements.txt
```

### 5. Prepare the database and sample products

```powershell
python manage.py migrate
python manage.py seed_demo
python manage.py createsuperuser
```

Enter your preferred username, email, and password when prompted. This is the **platform superuser** and the only type of account allowed into Django Administration.

### 6. Create businesses and client accounts

Start the server, open [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/), and sign in with the platform superuser. Use **Stores → Add store** to enter the business, subscription dates, plan, status, and its initial administrator credentials in one form.

Store administrators and staff sign in at [http://127.0.0.1:8000/login/](http://127.0.0.1:8000/login/). Newly issued accounts sign in with their confirmed temporary password and must replace it before the workspace opens. Multi-store assignment and switching are Pro-only: while creating a Pro store, the platform administrator can select an administrator who already manages another Pro store, and that administrator can switch the entire workspace between assigned branches. Every store on a shared login must remain on Pro. Starter permits 1 administrator and 2 cashiers per store. Pro permits up to 3 administrators and 10 staff with Cashier, Inventory controller, or Manager access. Outside the one-store self-service Trial flow, only the platform superuser can create stores or change subscriptions.

Visitors can also select **Start a 30-day free trial** on the login page. This automatically creates exactly one Trial store and its administrator login without platform-admin involvement. Trial users cannot create more stores, add staff, or change their subscription. The public **Compare Starter & Pro** page links to a downloadable three-page PDF reference.

### 7. Start the website

```powershell
python manage.py runserver
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and sign in.

## Automated verification

```powershell
python manage.py check
python manage.py test
```

## Scheduled Pro summaries

The command below sends every due daily or weekly Pro summary using the configured Django email backend:

```powershell
python manage.py send_scheduled_reports
```

Run it daily with Windows Task Scheduler, cron, or the scheduler provided by your hosting platform. Expired and suspended stores are skipped automatically.

## Independent backup verification

Create an encrypted off-provider logical export from a trusted operator machine, then prove that it restores into an isolated temporary database:

```powershell
python manage.py backup_database --output D:\secure-backups\oxpos-YYYY-MM-DD.json.gz
python manage.py verify_database_backup --input D:\secure-backups\oxpos-YYYY-MM-DD.json.gz
```

See `docs/backup-restore-runbook.md` for the production-provider checklist, storage requirements, and restore-test recordkeeping. Do not use Vercel's temporary function filesystem as a backup destination.

## Password-reset email

The local default prints mail to the console. Production must use a real email provider. For Gmail SMTP, create a Google App Password and set the backend to `django.core.mail.backends.smtp.EmailBackend`, host `smtp.gmail.com`, port `587`, the Gmail address, App Password, TLS enabled, and a matching `DJANGO_DEFAULT_FROM_EMAIL`. Never commit or paste the App Password into source code.

## Important files

- `pos/models.py` — stores, roles, products, sales, customers, inventory movements, purchasing, and plan access
- `pos/views.py` — tenant-scoped checkout, Pro operations, reports, exports, and team logic
- `pos/forms.py` — validated Django forms
- `templates/` — all website pages
- `static/pos/css/app.css` — complete responsive design
- `static/pos/css/admin.css` — minimalist light platform-administration design
- `static/pos/js/sell.js` — interactive checkout cart
- `static/pos/docs/oxpos-feature-guide.pdf` — generated feature and plan guide
- `scripts/generate_feature_guide.py` — reproducible PDF generator
- `docs/pro-multi-store.md` — Pro business rules, tenant flow, owner-dashboard metrics, and maintenance checklist
- `docs/dining-workflow.md` — business-category eligibility and enforcement for dine-in, take-out, and service charges
- `docs/backup-restore-runbook.md` — provider checks, independent exports, and isolated restore verification
- `positive_pos/settings.py` — Django and SQLite configuration

## Production note

SQLite is suitable for local development and evaluation. Before hosting multiple businesses or concurrent terminals, configure PostgreSQL, HTTPS, automated backups, and SMTP email delivery.

Production configuration is environment-based. At minimum, set `DJANGO_DEBUG=False`, a long random `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and a persistent PostgreSQL `DATABASE_URL`. Local development continues to use SQLite when `DATABASE_URL` is absent. Configure the `DJANGO_EMAIL_*` values for your SMTP provider so password-reset messages reach users; the development default prints reset messages to the terminal. Secure cookies and HTTPS redirects default on when debug mode is off. If TLS terminates at a trusted reverse proxy, set `DJANGO_TRUST_PROXY_SSL_HEADER=True`. Review `.env.example` for every available security setting; Django does not automatically load that file, so set these values in your shell or hosting platform.

Vercel detects this Django project from `manage.py` and its WSGI application. The build hook in `pyproject.toml` applies migrations when Vercel supplies `DATABASE_URL`, and `.vercelignore` prevents the local SQLite database and development environment from being uploaded.
