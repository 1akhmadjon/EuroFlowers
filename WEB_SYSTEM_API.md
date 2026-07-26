# EuroFlowers Web System API

Base URL: `https://euroflowers.api.cognilabs.org/api/`

Auth: `POST /auth/token/`, then send `Authorization: Bearer <access>`.

## New Inventory Structure

### Suppliers

`/suppliers/`

Fields:
- `name` required
- `phone`
- `notes`
- `is_active`
- read-only `batches_count`
- read-only `total_received_stems`

Use:
- Admin creates suppliers here.
- When creating stock batch, pass `supplier` id.
- Admin can filter supplier history through stock batches and movements.

### Flower Stock Batches

`/stock-batches/`

Important fields:
- `variant`
- `supplier`
- `batch_number`
- `received_at`
- `received_stems`
- `received_bunches` optional write-only, `received_stems` bo'lmasa backend `received_bunches * stems_per_bunch` qilib hisoblaydi
- `remaining_stems`
- `stems_per_bunch`
- `cost_per_stem`
- `sale_price_per_stem`
- `sale_price_per_bunch`
- `minimum_sale_stems`

When stock batch is created:
- `StockMovement` with `movement_type=in` is created.
- If `supplier` is present, admin gets `supplier_stock` notification by WebSocket.
- If Telegram group is configured, Telegram notification is also sent.

### Stock Movements

`/stock-movements/`

Filters:
- `batch`
- `supplier`
- `movement_type`
- `created_at_after`
- `created_at_before`

Use this for:
- batch-level report
- supplier history
- waste report
- catalog/custom production report

Waste or manual movement:

`POST /stock-batches/{id}/movement/`

Body with stems:
```json
{
  "movement_type": "waste",
  "quantity_stems": 10,
  "reason": "Chiqit"
}
```

Body with bunches:
```json
{
  "movement_type": "waste",
  "quantity_bunches": "2.00",
  "reason": "Chiqit"
}
```

Backend converts bunches using selected batch `stems_per_bunch`.

## Materials

Use `/materials/` or `/packaging/`.

`packaging_type` values:
- `wrap` buket qog'ozi
- `basket` savat
- `box` quti
- `other` aksessuarlar, shokolad, maktub, o'yinchoq va boshqalar

Material movement:

`POST /materials/{id}/movement/`

Body:
```json
{
  "movement_type": "in",
  "quantity": 10,
  "reason": "Kirim"
}
```

## Florists

### Florist Profiles

`/florists/`

Fields:
- `user`
- `branch`
- `staff_type` as `florist` or `apprentice`
- `phone`
- `daily_pay`
- `work_start_time`
- `work_end_time`
- `shop_latitude`
- `shop_longitude`
- `arrival_radius_meters`
- `departure_radius_meters`
- `is_active`

Read-only:
- `salary_total`
- `catalog_count`

### Volume Rates

`/florist-volume-rates/`

Fields:
- `branch`
- `arrangement_type` as `bouquet` or `basket`
- `volume` as `small`, `medium`, `large`
- `default_stems`
- `florist_fee`
- `is_active`

Admin sets standard fees here. When catalog is created with `volume` and no explicit `florist_fee`, backend uses this rate.

### Salary Entries

`/florist-salary/`

Filters:
- `florist`
- `source`
- `work_date`
- `created_at_after`
- `created_at_before`

Sources:
- `catalog`
- `custom_catalog`
- `daily`
- `manual`

Salary is automatically created when catalog has `florist` and `florist_fee`.

## Catalog

`/catalog/`

New fields:
- `catalog_kind` as `standard` or `custom`
- `volume` as `small`, `medium`, `large`
- `florist`
- `florist_fee`
- read-only `calculated_cost_price`
- read-only `calculated_component_price`
- read-only `discount_amount`
- `composition`
- `materials`

### Standard Catalog Create

Use when supervisor adds florist-made ready bouquet/basket.

Body example:
```json
{
  "name_uz": "Gortenziya savat",
  "arrangement_type": "basket",
  "catalog_kind": "standard",
  "volume": "medium",
  "florist": 1,
  "price": "850000.00",
  "quantity_total": 1,
  "composition": [
    {"stock_batch": 12, "quantity_stems": 10, "quantity_bunches": "2.00"}
  ],
  "materials": [
    {"packaging": 5, "quantity": 1},
    {"packaging": 9, "quantity": 1}
  ]
}
```

Backend:
- deducts exact selected stock batch
- deducts selected materials
- calculates component price and discount
- creates florist salary entry

### Custom Catalog Create

Use when customer comes to shop and selects flowers/materials manually.

Body is the same, but:

```json
{
  "catalog_kind": "custom",
  "status": "sold",
  "price": "800000.00"
}
```

Backend:
- automatically sets `status=sold`
- sets `quantity_sold=quantity_total`
- deducts selected batches/materials
- calculates `discount_amount`
- adds florist salary

If selected components would normally cost `1000000` and actual selling price is `800000`, backend returns:

`discount_amount = 200000`

## Lead Behavior

If lead status becomes `won`:
- catalog quantity decreases through `quantity_sold`
- catalog flowers are not deducted again, because catalog creation already deducted stock

If lead moves from `won` to another status or is deleted:
- catalog quantity is restored
- custom/direct stock usage is restored
- packaging usage is restored

## Dashboard And Analytics

`GET /dashboard/?date_from=2026-07-01&date_to=2026-07-26`

`GET /analytics/?date_from=2026-07-01&date_to=2026-07-26`

New response blocks:
- `batch_inventory_stats`
- `florist_production_stats`
- `florist_salary_total`
- `catalog_revenue`
- `catalog_cost`
- `catalog_discount`
- `net_profit`

Use `batch_inventory_stats` to show:
- which supplier delivered the batch
- how many stems went to standard catalog
- how many stems went to custom catalog
- how many stems were waste

Use `florist_production_stats` to show:
- florist name
- standard bouquets/baskets
- custom bouquets/baskets
- salary total

## WebSocket

Connect:

`wss://euroflowers.api.cognilabs.org/ws/notifications/?token=<access>`

Supplier stock notification arrives as:

```json
{
  "type": "notification.created",
  "notification": {
    "notification_type": "supplier_stock"
  }
}
```
