# Frontend uchun oxirgi backend o‘zgarishlar

## Deploy

Backend productionga deploy qilingan.

API base:

```text
https://euroflowers.api.cognilabs.org/api
```

## 1. AI javob qoidalari

AI prompt backend tomonda kuchaytirildi:

- mijoz o‘zbekcha kirillda yozsa ham AI o‘zbek lotinida javob beradi;
- ruscha so‘zlarni o‘zbek javobga aralashtirmaydi;
- mijoz aniq rus tilida yozsa rus tilida javob beradi;
- custom buket/savat yig‘dirishda florist xizmati haqida aytadi:

```text
Florist xizmati 50 000 so‘mdan boshlanadi, gul obyomiga qarab o‘zgaradi.
```

- story/reel/post/katalogdagi tayyor gullarda florist pulini alohida aytmaydi.

## 2. Katalog quantity

`CatalogItem` yangi fieldlar:

```json
{
  "quantity_total": 10,
  "quantity_sold": 3,
  "quantity_stock_deducted": 3
}
```

Ma’nosi:

- `quantity_total`: katalogga qo‘yilgan tayyor buket/kompozitsiya soni;
- `quantity_sold`: sotilgan son;
- `quantity_stock_deducted`: skladdan yechilgan son.

Katalog create/update paytida backend sklad yetarliligini tekshiradi:

```text
quantity_total * composition.quantity_stems <= stock_batch.remaining_stems
```

Yetmasa `400` qaytadi.

## 3. Katalog sotish

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

- `quantity_sold`ni oshiradi;
- hammasi sotilgan bo‘lsa `status = sold`;
- qisman sotilgan bo‘lsa katalog sotuvda qoladi;
- sklad hali avtomatik kamaymaydi;
- sklad chiqimi kerakligi haqida notification yaratadi.

## 4. Katalogdan sklad kamaytirish

```http
POST /api/catalog/{id}/deduct_stock/
```

Body optional:

```json
{
  "quantity": 3
}
```

Body bo‘sh bo‘lsa sotilgan, lekin hali skladdan yechilmagan hamma son yechiladi.

Misol:

```text
quantity_total = 20
1 ta buketga 3 pochka atirgul ketgan
5 ta sotildi
deduct_stock qilinganda 5 * 3 pochka skladdan minus bo‘ladi
```

Stock logs:

```json
{
  "reference_type": "catalog_item",
  "reference_id": 12,
  "reason": "Buket nomi sotildi: 5 ta"
}
```

## 5. Lead yaratishda yangi mijoz

Endi lead yaratish uchun oldin customer create qilish shart emas.

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

- telefonni `+998901234567` formatga normalize qiladi;
- shu telefon bilan mijoz bo‘lsa o‘shanga lead bog‘laydi;
- mijoz bo‘lmasa yangi customer yaratadi;
- lead ichiga gul/material usage qatorlarini saqlaydi.

Response ichida:

```json
{
  "stock_usage": [],
  "packaging_usage": []
}
```

## 6. Lead sotildi bo‘lsa sklad kamayadi

Lead status `won`ga o‘tganda backend avtomatik sklad kamaytiradi.

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

- `stock_usage` bo‘yicha gul skladdan kamayadi;
- `packaging_usage` bo‘yicha material/savat skladdan kamayadi;
- `Lead.stock_deducted_at` to‘ldiriladi;
- stock loglarda `reference_type = lead`;
- qoldiq yetmasa `400` qaytadi va status update rollback bo‘ladi.

AI yaratgan leadlarda stock usage bo‘lmasligi mumkin. Operator leadni aniqlashtirib `stock_usage_input`, `packaging_usage_input`, `florist_fee` bilan update qilib, keyin `won` qilishi kerak.

## 7. Material sklad

Material sklad uchun alohida endpoint aliaslar qo‘shildi. Ichkarida `Packaging` modeli ishlaydi.

Types:

```text
wrap
basket
box
accessory
```

Endpointlar:

```http
GET /api/materials/
GET /api/materials/?packaging_type=basket
GET /api/materials/?packaging_type=accessory
POST /api/materials/
PATCH /api/materials/{id}/
POST /api/materials/{id}/movement/
GET /api/material-movements/
```

Oldingi endpointlar ham ishlaydi:

```http
GET /api/packaging/
POST /api/packaging/{id}/movement/
GET /api/packaging-movements/
```

## 8. Dashboard date filter va yangi stats

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

`pending_deductions` endi partial kataloglarni ham hisoblaydi:

```text
quantity_sold > quantity_stock_deducted
```

## 9. Frontend UI tavsiya

Inventory page ikki bo‘limga ajratiladi:

```text
Gul sklad
Material sklad
```

Katalog item card/table:

```text
Jami: quantity_total
Sotildi: quantity_sold
Skladdan yechildi: quantity_stock_deducted
Pending: quantity_sold - quantity_stock_deducted
```

Lead form:

```text
Mijoz ismi
Telefon
Gul usage rows
Material/savat usage rows
Florist fee
Estimated price
Status
```

Lead `won` qilinishidan oldin usage rows to‘ldirilganini UI’da operatorga eslatish kerak.
