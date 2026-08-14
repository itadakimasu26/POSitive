# Dining workflow by business category

OXPOS exposes dine-in, take-out, and dine-in service charges only when
`StoreSettings.store_type` is `Cafe`. Grocery, fashion, electronics, pet,
hardware, beauty, and general-retail stores use a standard `Retail` sale.

## Enforcement points

- `StoreSettings.supports_dining` is the single model-level capability flag.
- Store and trial forms reject a non-zero service charge for non-Cafe stores.
- `StoreSettings.save()` normalizes the rate to zero for non-Cafe direct writes.
- `complete_sale()` ignores crafted dine-in/take-out values for non-Cafe stores
  and saves the transaction as `Retail` with no service charge.
- Checkout, receipts, reports, and CSV exports show dining details only for a
  Cafe store.
- Offline imports use `Take-out` for Cafe stores and `Retail` for every other
  category because the recovery CSV does not collect a dining selection.

When adding a new food-service store category, update
`StoreSettings.supports_dining` rather than scattering new string comparisons
through forms, views, and templates. Add tests for its checkout visibility,
server-side order normalization, service-charge calculation, receipt, report,
and export behavior.
