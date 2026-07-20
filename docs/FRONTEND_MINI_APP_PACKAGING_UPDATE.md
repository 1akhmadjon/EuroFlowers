# Frontend Update: Telegram Mini App + Savat Sklad

## 1. Telegram Mini App

Mini app endpointlari Telegram `initData` bilan ishlaydi. Frontend Telegram WebApp ichida:

```ts
const initData = window.Telegram.WebApp.initData
```

shu qiymatni backendga `init_data` sifatida yuboradi.

Backendda `telegram_bot_token` sozlangan bo‘lsa, yuborilgan `init_data` HMAC orqali tekshiriladi. Noto‘g‘ri bo‘lsa `403` qaytadi.

`/api/mini-app/me/` va `/api/mini-app/leads/` uchun real Telegram `init_data` majburiy. `/api/mini-app/catalog/` va `/api/mini-app/quote/` init_data bo‘lmasa ham katalog/quote uchun ishlaydi, lekin customer history qaytmaydi.

### Customer + Order History

```http
GET /api/mini-app/me/?init_data=...
```

Response:

```json
{
  "customer": {
    "id": 14,
    "name": "Ali",
    "phone": "+998901234567",
    "instagram_user_id": "miniapp:777"
  },
  "orders": [
    {
      "id": 25,
      "created_at": "2026-07-20T16:00:00+05:00",
      "updated_at": "2026-07-20T16:00:00+05:00",
      "status": "new",
      "status_label": "Yangi",
      "source": "mini_app",
      "arrangement_type": "basket",
      "request": "Mini app buyurtma...",
      "estimated_price": "230000.00",
      "details": {
        "lines": [
          {
            "type": "stock",
            "id": 1,
            "flower_uz": "Gortenziya",
            "flower_ru": "Гортензия",
            "variant_uz": "Blue",
            "color_uz": "Moviy",
            "quantity_stems": 3,
            "price_per_stem": "20000.00",
            "total": "60000.00"
          }
        ],
        "packaging": {},
        "florist_fee": "50000.00",
        "estimated_price": "230000.00",
        "price_is_estimate": true,
        "note": "Bugun kerak"
      }
    }
  ]
}
```

`customer = null` va `orders = []` bo‘lishi mumkin, agar mijoz hali mini appdan buyurtma bermagan bo‘lsa.

### Catalog

```http
GET /api/mini-app/catalog/?init_data=...&branch=1
```

Response endi katalog bilan birga customer/order history ham qaytaradi:

```json
{
  "catalog": [],
  "stock": [],
  "packaging": [],
  "customer": null,
  "orders": []
}
```

Frontend birinchi ekranda bitta request bilan katalog, sklad, savat/qadoq va mijoz tarixini olishi mumkin.

### Quote

```http
POST /api/mini-app/quote/
```

Body:

```json
{
  "init_data": "...",
  "branch": 1,
  "arrangement_type": "basket",
  "items": [
    {"stock_batch": 1, "quantity_stems": 3}
  ],
  "packaging": 1
}
```

Response:

```json
{
  "lines": [],
  "packaging": {},
  "florist_fee": "50000.00",
  "estimated_price": "230000.00",
  "price_is_estimate": true
}
```

### Lead Create

```http
POST /api/mini-app/leads/
```

Body:

```json
{
  "init_data": "...",
  "branch": 1,
  "arrangement_type": "basket",
  "items": [
    {"stock_batch": 1, "quantity_stems": 3}
  ],
  "packaging": 1,
  "name": "Ali",
  "phone": "901234567",
  "note": "Bugun kerak"
}
```

Backend:

- phone ni `+998901234567` formatga normalize qiladi;
- `Customer.instagram_user_id = miniapp:{telegram_user_id}` bo‘yicha mijozni topadi yoki yaratadi;
- har safar yangi `Lead` yaratadi;
- lead `details` ichiga structured quote lines saqlaydi;
- `source = mini_app`;
- notification yaratadi.

Yangi lead response oddiy `LeadSerializer` orqali qaytadi, lekin unda `details` field ham bor.

## 2. Savat/Qadoq Sklad

`Packaging` endi sklad elementi sifatida yuritiladi. `quantity` current qoldiq.

Types:

```text
wrap
basket
box
accessory
```

### Packaging List/Create/Edit

```http
GET /api/packaging/?packaging_type=basket
POST /api/packaging/
PATCH /api/packaging/{id}/
```

Create paytida `quantity > 0` bo‘lsa backend avtomatik `in` movement yozadi.

`PATCH quantity` o‘zgarsa backend avtomatik `adjustment` movement yozadi.

### Manual Movement

```http
POST /api/packaging/{id}/movement/
```

Body:

```json
{
  "movement_type": "out",
  "quantity": 3,
  "reason": "Savat sotildi"
}
```

Movement types:

```text
in
out
adjustment
waste
transfer_out
transfer_in
```

Rules:

- `in`, `transfer_in` qoldiqni oshiradi;
- `out`, `waste`, `transfer_out` qoldiqni kamaytiradi;
- `adjustment` signed quantity oladi: `5` qoldiqni 5 taga oshiradi, `-2` qoldiqni 2 taga kamaytiradi;
- qoldiq yetmasa `400` qaytadi;
- response `PackagingMovementSerializer`.

### Movement Journal

```http
GET /api/packaging-movements/
```

Filters:

```http
GET /api/packaging-movements/?packaging=1
GET /api/packaging-movements/?movement_type=out
GET /api/packaging-movements/?created_at_after=2026-07-20T00:00:00%2B05:00
GET /api/packaging-movements/?created_at_before=2026-07-21T00:00:00%2B05:00
```

Response item:

```json
{
  "id": 1,
  "packaging": 1,
  "packaging_detail": {},
  "movement_type": "out",
  "quantity": -3,
  "reason": "Savat sotildi",
  "performed_by": 1,
  "performed_by_detail": {},
  "created_at": "2026-07-20T16:00:00+05:00"
}
```

Frontendda savat sklad sahifasida:

- `Packaging.quantity` current qoldiq sifatida ko‘rsatiladi;
- kirim/chiqim uchun `POST /api/packaging/{id}/movement/` ishlatiladi;
- journal uchun `/api/packaging-movements/` ishlatiladi.
