# flyapp — Shop Management Skill

You manage a business on flyapp.so. Use the API endpoints below to handle orders, products, inventory, customers, analytics, and deliveries.

## Authentication

All requests require the `X-API-Key` header. Read your API key from `TOOLS.md` in your workspace.

```bash
curl -s -H "X-API-Key: YOUR_API_KEY" https://flyapp.so/api/ENDPOINT
```

## API Reference

Base URL: `https://flyapp.so/api`

---

### Orders

**List orders**
```
GET /orders/?status=pending|ready|done|all
```

**Get order details**
```
GET /orders/{public_id}/intern
```

**Create draft order**
```
POST /orders/draft
Content-Type: application/json

{
  "method": "delivery",
  "price": 75.00,
  "note": "Red roses bouquet",
  "delivery_price": 15.00
}
```

**Update order status**
```
POST /orders/{public_id}/status
Content-Type: application/json

{"status": "ready"}
```
Statuses: `pending` → `ready` → `done`

**Mark order as paid**
```
POST /orders/{public_id}/paid
Content-Type: application/json

{"payment_method": "cash"}
```

**Update order note**
```
POST /orders/{public_id}/note
Content-Type: application/json

{"note": "Updated delivery instructions"}
```

**Assign order to staff**
```
POST /orders/{public_id}/assign
Content-Type: application/json

{"assigned_to": "username"}
```

**Delete order**
```
DELETE /orders/{public_id}
```

**Calendar view**
```
GET /orders/calendar?start_date=2026-02-01&end_date=2026-02-28
```

---

### Products

**List products**
```
GET /products/
```

**Get product**
```
GET /products/{product_id}
```

**Create product**
```
POST /products/
Content-Type: application/json

{
  "name": "Red Roses Bouquet",
  "price": 65.00,
  "description": "12 premium red roses",
  "current_quantity": 10,
  "is_featured": true
}
```

**Update product**
```
PATCH /products/{product_id}
Content-Type: application/json

{"price": 70.00, "current_quantity": 8}
```

**Delete product**
```
DELETE /products/{product_id}
```

---

### Inventory / Stock

**Dashboard stats**
```
GET /stock/dashboard
```
Returns: total_inventory_value, total_item_types, low_stock_count, recent_waste_value

**Current inventory levels**
```
GET /stock/current
```

**Aggregated inventory by item type**
```
GET /stock/aggregated
```

**List stock entries**
```
GET /stock/?entry_type=purchase|waste|adjustment&limit=50
```

**Add stock entry**
```
POST /stock/
Content-Type: application/json

{
  "item_type_id": 1,
  "item_name": "Red Roses",
  "quantity": 50,
  "unit_cost": 2.50,
  "entry_type": "purchase"
}
```
Entry types: `purchase`, `waste`, `adjustment`

**Stock take snapshot**
```
GET /stock/stock-take/snapshot
```

**Submit stock take**
```
POST /stock/stock-take/submit
Content-Type: application/json

{
  "adjustments": [
    {"item_type_id": 1, "actual_quantity": 45}
  ]
}
```

---

### Customers

**List customers**
```
GET /customers/?page=1&page_size=20&search=john&sort_by=name&sort_order=asc
```

**Customer orders**
```
GET /customers/{customer_id}/orders
```

---

### Analytics

**Business analytics**
```
GET /analytics/?start=2026-02-01T00:00:00&end=2026-02-28T23:59:59
```
Returns: overview stats, top customers, order distribution, monthly stats, subscription stats

---

### Delivery

**Get delivery quotes**
```
POST /delivery/{order_id}/quotes?location_id=1
```

**Create and dispatch delivery**
```
POST /delivery/{order_id}/request
Content-Type: application/json

{
  "quote_id": "quote_abc123",
  "tip": 5.00,
  "dropoff_phone": "+15551234567",
  "dropoff_notes": "Ring doorbell"
}
```

**Check delivery status**
```
GET /delivery/{order_id}/status
```

**Cancel delivery**
```
POST /delivery/{order_id}/cancel
```

---

## Usage Guidelines

- Always check current state before making changes (list before update)
- Use concise responses — the user is on Telegram
- When listing orders/products, summarize key info (don't dump raw JSON)
- Format currency amounts properly
- Confirm destructive actions (delete, cancel) before executing
