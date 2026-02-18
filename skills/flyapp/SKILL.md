---
name: flyapp
description: Manage a business on flyapp.so via its API.
---

# flyapp API Reference

Auth: `X-API-Key: <token>` header. ALL paths MUST end with `/`. Pagination: `?limit=100&offset=0`.
Use `wget --header="X-API-Key: <token>" -qO- "<url>"` for GET.
For POST: `wget --header="X-API-Key: <token>" --header="Content-Type: application/json" --post-data='{"key":"val"}' -qO- "<url>"`

**IMPORTANT**: All `{id}` params below use the order's `public_id` (e.g. "msXFv2"), NOT the numeric `id`.

## Orders
- `GET /orders/?status={pending|ready|done|all}` → list
- `GET /orders/{public_id}/intern/` → detail
- `POST /orders/{public_id}/status/` ← `{"status":"pending"|"ready"|"done"|"failed"|"refunded"}`
- `POST /orders/{public_id}/paid/` ← `{"payment_method":"manual"}`
- `POST /orders/{public_id}/note/` ← `{"note":"..."}`
- `POST /orders/{public_id}/assign/` ← `{"assigned_to":"username"}`
- `DELETE /orders/{public_id}/`
- `GET /orders/calendar/?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- Lifecycle: draft→pending→ready→done (or →failed→refunded)

## Delivery
- `POST /delivery/{public_id}/quotes/?location_id=1&schedule=true` → quotes
- `POST /delivery/{public_id}/request/` ← `{quote_id, tip?(cents), dropoff_phone?, dropoff_notes?}`
- `POST /delivery/{public_id}/cancel/`
- `GET /delivery/{public_id}/status/` → tracking_url, courier, ETAs

## Products
- `GET /products/` → list | `GET /products/{id}/` → detail (numeric id ok here)
- `POST /products/` ← name, price, description?, current_quantity?, category_ids[]?
- `PATCH /products/{id}/` ← any fields | `DELETE /products/{id}/`

## Inventory
- `GET /stock/dashboard/` → totals, low_stock
- `GET /stock/current/` → [{id, name, quantity, unit_cost}]
- `POST /stock/` ← `{item_name, quantity, unit_cost, entry_type:"purchase"|"waste"|"adjustment"}`

## Categories
- `GET /categories/` | `POST /categories/` ← `{name}` | `DELETE /categories/{id}/`

## Customers
- `GET /customers/?page=1&page_size=20&sort_by=last_order_date&sort_order=desc&search=`
- `GET /customers/{id}/orders/`

## Analytics
- `GET /analytics/?start=ISO&end=ISO` → overview, top customers, monthly stats
- `GET /revenue/?start=YYYY-MM-DD&end=YYYY-MM-DD`

## Business
- `GET /business/` | `PUT /business/` ← `{name?, phone?, email?}`
- `GET /business/locations/` | `POST /business/locations/`
