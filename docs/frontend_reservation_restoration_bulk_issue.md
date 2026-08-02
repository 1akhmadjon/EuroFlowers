# EuroFlowers Frontend Changes

## 🌸 Katalog restavratsiya

Endpoint:

`POST /api/catalog/{catalog_id}/restore-flowers/`

Payload:

```json
{
  "florist": 6,
  "old_batch": 79,
  "new_batch": 103,
  "quantity_stems": 15,
  "reason": "Restavratsiya"
}
```

Frontend kerak:

- Katalog detail ichida `Restavratsiya` action.
- Eski gul `composition` ichidan tanlanadi.
- Yangi gul sklad batchlardan tanlanadi.
- Florist majburiy tanlanadi.
- Natija: eski gul chiqitga ketadi, yangi gul floristga restavratsiya uchun chiqariladi, katalog tarkibi yangilanadi.

## 📌 Bron

Endpoint:

`/api/reservations/`

Bron yaratish:

```json
{
  "customer_name": "Mijoz",
  "customer_phone": "901112233",
  "request_uz": "Qizil atirgul bron",
  "arrangement_type": "bouquet",
  "estimated_price": "500000",
  "desired_date": "2026-08-05",
  "desired_time": "18:00",
  "fulfillment": "delivery",
  "delivery_address": "Manzil"
}
```

To‘lov qo‘shish:

`POST /api/reservations/{id}/add-payment/`

```json
{
  "amount": "200000",
  "method": "cash",
  "note": "Zaklad"
}
```

Frontend kerak:

- Bronlar sahifasi: status, payment_status, mijoz, sana, so‘rov, to‘langan summa, qolgan summa.
- Bron detailda payment history ko‘rsatish.
- `cancel` action: `POST /api/reservations/{id}/cancel/`.

## 🧾 Bron bilan katalog sotish

Katalog sotishda optional `reservation` yuboriladi:

`POST /api/catalog/{id}/sell/`

```json
{
  "quantity": 1,
  "sale_price": "500000",
  "reservation": 12,
  "payment_type": "cash"
}
```

Frontend kerak:

- Sotish modalida bron tanlash.
- Bron tanlansa oldindan to‘langan summa va qolgan to‘lov ko‘rsatish.
- Backend historyga bron ID, paid_amount, remaining_due yozadi.

## 💐 Floristga bulk gul chiqarish

Endpoint:

`POST /api/florist-stock-issues/bulk-issue/`

```json
{
  "florist": 6,
  "items": [
    {"batch": 101, "quantity_stems": 25},
    {"batch": 103, "quantity_stems": 50}
  ],
  "reason": "Bugungi ish uchun"
}
```

Frontend kerak:

- Floristga chiqarish modalida bir nechta gul row qo‘shish.
- Har row: batch tanlash + dona soni.
- Bitta submit bilan hammasi chiqadi.
- Backend transaction: bitta rowda qoldiq yetmasa hech biri chiqmaydi.

## 📊 Hisob-kitob

- Accounting responsega `reservation_payments_summary` va `reservation_payments` qo‘shildi.
- Excel exportda `Bron to‘lovlari` sheet qo‘shildi.
- Katalog sotilganda full sale price savdoga kiradi.
- Zaklad/to‘liq bron to‘lovlari alohida cashflow sifatida ko‘rinadi.
