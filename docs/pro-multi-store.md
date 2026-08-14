# Pro multi-store architecture

This document describes the business rules and request flow behind OXPOS's
Pro multi-store experience. It is intended for maintainers changing stores,
memberships, subscription plans, or tenant-scoped views.

## Product rules

- Trial and Starter are single-store plans.
- A user may have active memberships in more than one store only when every
  assigned store is on Pro.
- The platform administrator assigns an existing Pro administrator while
  creating or editing a Pro store in Django Administration.
- A Pro store with users shared across stores cannot be downgraded until those
  cross-store memberships are removed.
- The owner super dashboard lists only stores where the signed-in user has an
  active Administrator membership. It never lists every store in the database.

These rules are enforced in the model layer, not only in the interface. This is
important because memberships can also be created through forms, Django Admin,
management commands, or future APIs.

## Request and tenant flow

1. `load_store_access()` loads the user's active memberships and selects the
   store saved in the session.
2. It sets `request.store`, `request.store_membership`, and
   `request.available_memberships` for downstream views and templates.
3. It exposes `request.multi_store_enabled` only when the user has multiple
   assignments and every assigned store is Pro.
4. `switch_store()` verifies the requested membership again, enforces the
   all-Pro rule, updates the session, and redirects to an allowed local URL.
5. Store views query through `request.store`, so changing stores changes the
   complete data scope for products, sales, reports, customers, and settings.

Do not accept a store ID directly from the browser and use it in business-data
queries. Always resolve it through the signed-in user's active membership or
continue using `request.store`.

## Owner super dashboard

`multi_store_dashboard()` is protected by administrator and Pro checks. It
aggregates only the stores administered by the current user and presents:

- today's and trailing 30-day revenue and order totals;
- inventory units, active products, low stock, and out-of-stock counts;
- active customer, administrator, and staff counts;
- subscription status and expiry date;
- open cash-register ownership and purchase orders awaiting receipt;
- last completed sale, portfolio attention items, and recent audit activity;
- tenant-safe shortcuts to Dashboard, Sell, Reports, and Products.

Shortcuts for another store submit to `switch_store()` before redirecting. This
keeps the session and all destination queries aligned to the chosen branch.

## Where to update plan descriptions

When Pro capabilities change, keep these sources synchronized:

- `templates/pos/marketing_home.html` for the public homepage;
- `templates/pos/feature_guide.html` for the online plan comparison;
- `templates/pos/settings.html` and `templates/pos/pro_required.html` for the
  signed-in product experience;
- `scripts/feature_guide_source.html` for the printable guide;
- `static/pos/docs/oxpos-feature-guide.pdf`, regenerated with
  `python scripts/generate_feature_guide.py`;
- `README.md` and this document for maintainers.

## Verification checklist

After changing these rules or metrics:

1. Run `python manage.py check`.
2. Run `python manage.py makemigrations --check --dry-run`.
3. Run the multi-store, team-access, and platform-administration tests.
4. Run the complete Django test suite.
5. Regenerate the PDF if customer-facing plan descriptions changed.
6. Verify an owner cannot see or switch into an unassigned store.
7. Verify a Starter or Trial membership cannot become part of a shared login.
