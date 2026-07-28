# EuroFlowers Frontend API Notes

Sana: 2026-07-28

## 1. Katalog sotuvda to'lov turi

Standart katalogni sotildi qilish:

`POST /api/catalog/{id}/sell/`

Body:

```json
{
  "quantity": 1,
  "sale_price": "450000.00",
  "payment_type": "card",
  "discount_reason": "VIP mijoz"
}
```

`payment_type` qiymatlari:

- `cash` - naqd
- `card` - karta

`sale_price` optional. Berilmasa katalogdagi `price` ishlatiladi.

`discount_reason` faqat `sale_price` katalog narxidan arzon bo'lsa majburiy.

Custom katalog create qilayotganda ham `payment_type` yuborish mumkin. Custom katalog auto `sold` bo'lgani uchun payment history shu paytda yoziladi.

## 2. Hisob-kitob Page

Admin hisob-kitob uchun yangi endpoint:

`GET /api/accounting/?date_from=2026-07-01&date_to=2026-07-28`

Filterlar:

- `date_from` optional, format `YYYY-MM-DD`
- `date_to` optional, format `YYYY-MM-DD`
- `from` alias sifatida ishlaydi
- `to` alias sifatida ishlaydi

Response asosiy bloklari:

```json
{
  "period": {
    "date_from": "2026-07-01",
    "date_to": "2026-07-28"
  },
  "summary": {
    "total_sales": "0.00",
    "cash_total": "0.00",
    "card_total": "0.00",
    "unknown_total": "0.00",
    "total_quantity": 0,
    "standard_quantity": 0,
    "custom_quantity": 0,
    "discount_total": "0.00",
    "discounted_sales_count": 0,
    "discounted_quantity": 0,
    "cost_total": "0.00",
    "net_profit": "0.00"
  },
  "by_kind": [],
  "by_payment": [],
  "by_volume": [],
  "discounted_sales": [],
  "history": []
}
```

Frontend page tavsiyasi:

- Yuqorida date range picker.
- KPI cards: umumiy savdo, naqd, karta, sof foyda, umumiy skidka.
- Chart/table: standard/custom sotilgan soni.
- Chart/table: hajm bo'yicha sotuvlar, `by_volume`.
- Table: `history`, sotilgan vaqt, katalogga qo'shilgan vaqt, katalog nomi, florist, to'lov turi, skidka izohi.
- Alohida tab yoki modal: `discounted_sales`.

## 3. Excel Exportlar

Florist o'z hisoboti:

`GET /api/exports/florist/?date_from=2026-07-01&date_to=2026-07-28`

Admin hamma floristlar:

`GET /api/exports/florists/?date_from=2026-07-01&date_to=2026-07-28`

Admin hisob-kitob/profit:

`GET /api/exports/profit/?date_from=2026-07-01&date_to=2026-07-28`

Excel fayllar serverda saqlanmaydi, response file sifatida qaytadi.

Frontend `blob` qilib download qilishi kerak.

Florist export ichida:

- Asosiy salary list
- Kunlik hajm sheet
- Keldi-ketdi sheet

Admin florist export ichida:

- Floristlar summary
- Kunlik hajm sheet, har florist bo'yicha

Admin profit export ichida:

- Hisob-kitob summary
- Hajmlar
- Sotuv history
- Skidkalar

## 4. Katalog Note

Katalog create/update body ichida `note` yuborish mumkin:

```json
{
  "name_uz": "Gortenziya savat",
  "note": "Ichki izoh yoki nazoratchi izohi"
}
```

`note` catalog detail/list response ichida ham qaytadi.

## 5. AI Sklad FIFO Qoida

Backend AI stock tool endi bir xil gul turi, navi va rang bo'yicha faqat eng birinchi kelgan faol partiyani qaytaradi.

Misol:

- Atirgul Jumilia partiya 1 bor
- Atirgul Jumilia partiya 2 bor
- Atirgul Jumilia partiya 3 bor

AI mijozga faqat partiya 1 narx/qoldiq ma'lumotini ko'rsatadi. Partiya 1 tugagandan keyin keyingi partiya ko'rinadi.

Frontend tomonda qo'shimcha ish kerak emas.
