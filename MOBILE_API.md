# EuroFlowers Mobile API

Base URL: `https://euroflowers.api.cognilabs.org/api/`

Auth: `POST /auth/token/`, then use `Authorization: Bearer <access>`.

## Roles And Permissions

Backend is page-permission based.

Mobile should show pages/actions based on `/me/` and permissions.

Recommended mobile pages:
- Florist profile
- Florist attendance
- Florist salary dashboard
- Supervisor catalog create
- Admin suppliers
- Admin stock
- Admin florist management
- Admin analytics

## Florist Mobile

### My Profile

`GET /florists/me/`

Returns florist profile:
- `staff_type`
- `daily_pay`
- `work_start_time`
- `work_end_time`
- shop coordinates
- radius settings

### My Dashboard

`GET /florists/me/dashboard/?date_from=2026-07-01&date_to=2026-07-26`

Returns:
- `salary_total`
- `salary_entries_count`
- `catalog_count`
- `custom_catalog_count`
- `attendance_days`
- `latest_salary_entries`
- `latest_attendance`

### Check In

Mobile app decides geofence logic.

Rule on mobile:
- if user stays within 30-50 meters for 10+ minutes, send check-in
- `checked_at` should be the timestamp when 10-minute valid stay started

Endpoint:

`POST /florist-attendance/check-in/`

Body:
```json
{
  "checked_at": "2026-07-26T09:05:00+05:00",
  "latitude": "41.299500",
  "longitude": "69.240100",
  "source": "mobile",
  "note": "Auto check-in"
}
```

### Check Out

Mobile app decides geofence logic.

Rule on mobile:
- near work end, if user is 50-80+ meters away for 30+ minutes, send check-out
- `checked_at` should be the timestamp when away period started

Endpoint:

`POST /florist-attendance/check-out/`

Body:
```json
{
  "checked_at": "2026-07-26T18:10:00+05:00",
  "latitude": "41.300500",
  "longitude": "69.250500",
  "source": "mobile",
  "note": "Auto check-out"
}
```

If florist is `apprentice`, check-in creates daily salary entry from `daily_pay`.

## Admin Mobile

### Manual Attendance

Admin can manually check-in/check-out any florist by passing `florist`.

`POST /florist-attendance/check-in/`

```json
{
  "florist": 3,
  "checked_at": "2026-07-26T09:00:00+05:00",
  "source": "manual",
  "note": "Admin qo'lda kiritdi"
}
```

`POST /florist-attendance/check-out/`

```json
{
  "florist": 3,
  "checked_at": "2026-07-26T18:00:00+05:00",
  "source": "manual",
  "note": "Admin qo'lda kiritdi"
}
```

### Suppliers

`GET /suppliers/`

`POST /suppliers/`

```json
{
  "name": "Ali Flowers",
  "phone": "+998901234567",
  "notes": "Gollandiya gortenziyalari",
  "is_active": true
}
```

### Stock Batch With Supplier

`POST /stock-batches/`

```json
{
  "variant": 10,
  "supplier": 2,
  "batch_number": "EF-260726-01",
  "received_at": "2026-07-26",
  "height_cm": 50,
  "stems_per_bunch": 5,
  "received_stems": 100,
  "remaining_stems": 100,
  "cost_per_stem": "60000.00",
  "sale_price_per_stem": "105000.00",
  "sale_price_per_bunch": "500000.00",
  "minimum_sale_stems": 1
}
```

Backend creates supplier notification automatically.

If mobile wants to enter by bunch:

```json
{
  "variant": 10,
  "supplier": 2,
  "batch_number": "EF-260726-02",
  "received_at": "2026-07-26",
  "height_cm": 50,
  "stems_per_bunch": 5,
  "received_bunches": "20.00",
  "cost_per_stem": "60000.00",
  "sale_price_per_stem": "105000.00",
  "sale_price_per_bunch": "500000.00",
  "minimum_sale_stems": 1
}
```

Backend calculates `received_stems=100` and `remaining_stems=100`.

### Waste By Batch

`POST /stock-batches/{id}/movement/`

```json
{
  "movement_type": "waste",
  "quantity_bunches": "1.00",
  "reason": "Chiqit"
}
```

## Supervisor Mobile

### Standard Catalog Create

Use when florist made a ready bouquet/basket and supervisor adds it.

`POST /catalog/`

```json
{
  "name_uz": "Pion buket",
  "arrangement_type": "bouquet",
  "catalog_kind": "standard",
  "volume": "medium",
  "florist": 1,
  "price": "900000.00",
  "quantity_total": 1,
  "composition": [
    {"stock_batch": 15, "quantity_stems": 10, "quantity_bunches": "2.00"}
  ],
  "materials": [
    {"packaging": 4, "quantity": 1}
  ]
}
```

Mobile must let supervisor choose exact batch after flower/variant selection.

### Custom Catalog Create

Use when customer comes to shop and manually selects flowers/accessories.

`POST /catalog/`

```json
{
  "name_uz": "Custom buket",
  "arrangement_type": "bouquet",
  "catalog_kind": "custom",
  "volume": "large",
  "florist": 1,
  "price": "800000.00",
  "quantity_total": 1,
  "composition": [
    {"stock_batch": 12, "quantity_stems": 10, "quantity_bunches": "2.00"},
    {"stock_batch": 20, "quantity_stems": 10, "quantity_bunches": "2.00"}
  ],
  "materials": [
    {"packaging": 4, "quantity": 1},
    {"packaging": 8, "quantity": 1}
  ]
}
```

Backend:
- marks it sold automatically
- deducts selected stock batches
- deducts materials
- calculates discount
- adds florist salary

## Analytics Mobile

`GET /dashboard/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`

`GET /analytics/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`

Use:
- `batch_inventory_stats` for supplier/batch report
- `florist_production_stats` for florist work report
- `net_profit` for profit
- `catalog_discount` for total discounts
- `florist_salary_total` for salary total

## Realtime

Connect:

`wss://euroflowers.api.cognilabs.org/ws/notifications/?token=<access>`

Use it for:
- supplier stock arrival notification
- lead changes
- chat messages
- general notifications
