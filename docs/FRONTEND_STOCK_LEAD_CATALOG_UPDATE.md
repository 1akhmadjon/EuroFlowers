# Frontend Update: Catalog Quantity, Lead Stock Deduction, Dashboard Stats

## 1. AI Behaviour

Backend AI promptga qo‘shimcha qat’iy qoidalar qo‘shildi:

- mijoz o‘zbekcha kirill yozsa ham AI o‘zbek lotinida javob beradi;
- ruscha so‘zlarni o‘zbek javobga aralashtirmaydi;
- faqat mijoz aniq rus tilida yozsa rus tilida javob beradi;
- custom buket/savat yig‘dirishda florist xizmati haqida aytadi: `50 000 so‘mdan boshlanadi, gul obyomiga qarab o‘zgaradi`;
- story/reel/post/katalogdagi tayyor gullar uchun florist xizmatini alohida aytmaydi, chunki bu tayyor yasalgan sotuvdagi gullar;
- AI lead yaratganda florist summasini `estimated_price` ichiga majburan qo‘shmaydi, operator/manual CRM flowda `florist_fee` alohida yuritiladi.

## 2. Catalog Quantity

`CatalogItem` yangi fieldlar:

```json
{
  "quantity_total": 10,
  "quantity_sold": 3,
  "quantity_stock_deducted": 3
}
```

Ma’nosi:

- `quantity_total`: katalogga nechta tayyor buket/kompozitsiya qo‘yilgan;
- `quantity_sold`: nechtasi sotildi;
- `quantity_stock_deducted`: nechtasi uchun gul skladdan yechildi.

### Create/Update Validation

Katalog create/update paytida backend tekshiradi:

```text
quantity_total * composition.quantity_stems <= stock_batch.remaining_stems
```

Yetmasa `400` qaytadi.

### Sell Quantity

```http
POST /api/catalog/{id}/sell/
```

Body:

```json
{
  "quantity": 3
}
```

Body bo‘sh bo‘lsa `quantity = 1`.

Backend:

- `quantity_sold += quantity`;
- hammasi sotilgan bo‘lsa `status = sold`;
- qisman sotilgan bo‘lsa item sotuvda qoladi;
- stock hali avtomatik kamaymaydi;
- notification yaratadi: sklad chiqimi kutilmoqda.

### Deduct Stock

```http
POST /api/catalog/{id}/deduct_stock/
```

Body optional:

```json
{
  "quantity": 3
}
```

Body bo‘sh bo‘lsa sotilgan, lekin hali skladdan yechilmagan hamma quantity yechiladi.

Misol:

- katalogda `quantity_total = 20`;
- har bitta buket uchun `3 pochka/75 dona atirgul`;
- `sell quantity=5`;
- `deduct_stock` qilinganda `5 * 75 = 375 dona` atirgul skladdan minus bo‘ladi.

Stock movement:

```json
{
  "reference_type": "catalog_item",
  "reference_id": 12,
  "reason": "Buket nomi sotildi: 5 ta"
}
```

## 3. Manual Lead Create With New Customer

Endi lead yaratishda oldin alohida customer create qilish shart emas.

```http
POST /api/leads/
```

Body:

```json
{
  "branch": 1,
  "status": "new",
  "request_uz": "3 pochka Freedom atirguldan savat",
  "arrangement_type": "basket",
  "estimated_price": "1750000.00",
  "florist_fee": "50000.00",
  "customer_name": "Ali",
  "customer_phone": "901234567",
  "stock_usage_input": [
    {
      "stock_batch": 1,
      "quantity_stems": 75,
      "quantity_bunches": "3.00"
    }
  ],
  "packaging_usage_input": [
    {
      "packaging": 4,
      "quantity": 1
    }
  ]
}
```

Backend:

- phone ni `+998901234567` formatga normalize qiladi;
- shu phone bilan customer bo‘lsa o‘shanga bog‘laydi;
- bo‘lmasa yangi customer yaratadi;
- lead yaratadi;
- lead ichiga gul/material usage qatorlarini saqlaydi.

Read response’da:

```json
{
  "stock_usage": [],
  "packaging_usage": []
}
```

## 4. Lead Sold -> Stock Deduction

Lead status `won` bo‘lganda backend lead ichidagi usage bo‘yicha skladni kamaytiradi.

```http
PATCH /api/leads/{id}/
```

Body:

```json
{
  "status": "won"
}
```

Backend:

- `Lead.stock_deducted_at` bo‘sh bo‘lsa deduction qiladi;
- `stock_usage` bo‘yicha `StockBatch.remaining_stems` kamayadi;
- `packaging_usage` bo‘yicha `Packaging.quantity` kamayadi;
- `StockMovement.reference_type = lead`;
- `PackagingMovement.reference_type = lead`;
- movement `reason` ichida lead id va mijoz ko‘rinadi;
- qoldiq yetmasa `400` qaytadi va status update rollback bo‘ladi.

AI yaratgan leadlarda hozircha stock usage bo‘lmasligi mumkin. Bunday leadni operator aniqlashtirib `stock_usage_input`, `packaging_usage_input`, `florist_fee` bilan update qilishi kerak, keyin `won` qiladi.

## 5. Material Sklad

Material sklad uchun alohida model ochilmadi, chunki `Packaging` endi to‘liq material sklad rolini bajaradi.

Types:

```text
wrap
basket
box
accessory
```

Gupka, lenta, quti, savat kabi narsalar `Packaging` ichida yuritiladi:

```http
GET /api/packaging/?packaging_type=accessory
GET /api/packaging/?packaging_type=basket
GET /api/materials/?packaging_type=accessory
GET /api/materials/?packaging_type=basket
```

Kirim/chiqim:

```http
POST /api/packaging/{id}/movement/
GET /api/packaging-movements/
POST /api/materials/{id}/movement/
GET /api/material-movements/
```

## 6. Dashboard Date Filters

Dashboard endi date range qabul qiladi:

```http
GET /api/dashboard/?from=2026-07-01&to=2026-07-20
```

Yoki datetime:

```http
GET /api/dashboard/?from=2026-07-01T00:00:00%2B05:00&to=2026-07-20T23:59:59%2B05:00
```

Yangi fields:

```json
{
  "period": {
    "from": "2026-07-01T00:00:00+05:00",
    "to": "2026-07-20T23:59:59+05:00"
  },
  "period_revenue": "12000000.00",
  "period_orders": 18,
  "period_leads": 52,
  "period_customers": 31,
  "period_conversations": 140,
  "florist_revenue": "900000.00",
  "flowers_sold_stems": 620
}
```

Existing fields saqlangan:

```text
active_leads
new_leads_today
orders_today
revenue_today
revenue_7d
conversion_rate
available_catalog
pending_deductions
stock_stems
low_stock
lead_pipeline
branch_stock
recent_leads
recent_notifications
```

`pending_deductions` endi partial kataloglarni ham hisoblaydi:

```text
quantity_sold > quantity_stock_deducted
```
