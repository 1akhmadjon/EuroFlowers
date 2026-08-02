# Frontend: Oformleniya Florist, Katalog Material, Sotuv Material

## Florist

`FloristProfile` ichiga yangi field qo'shildi:

- `decoration_fee` - oformleniya uchun 1 dona buket/savatga qo'shiladigan summa.

Florist yaratish yoki update qilishda shu fieldni yuborish mumkin.

## Katalog Qo'shish

`POST /api/catalog/`

Yangi optional fieldlar:

- `decoration_florist` - oformleniya qilgan florist id.
- `decoration_salary_amount` - read-only kabi ishlating; backend tanlangan floristning `decoration_fee` qiymatini avtomatik yozadi.
- `decoration_florist_detail` - response ichida decorator florist detail.

Katalog qo'shilganda:

- `materials[].quantity` 1 dona katalog uchun ishlatiladigan material soni.
- Backend material skladidan `materials[].quantity * quantity_total` miqdorni avtomatik kamaytiradi.
- `decoration_florist` tanlansa, decorator floristga `decoration_fee * quantity_total` salary yoziladi.
- `decoration_florist` tanlanmasa, oformleniya uchun hech kimga salary qo'shilmaydi.

Payload namunasi:

```json
{
  "name_uz": "Qizil buket",
  "arrangement_type": "bouquet",
  "volume": "L",
  "price": "1200000",
  "quantity_total": 10,
  "florist": 5,
  "decoration_florist": 8,
  "composition": [{"stock_batch": 12, "quantity_stems": 15}],
  "materials": [{"packaging": 3, "quantity": 1}]
}
```

## Katalog Sotish

`POST /api/catalog/{id}/sell/`

Yangi optional fieldlar:

- `materials` - sotuv vaqtida qo'shimcha ishlatilgan materiallar.
- `decoration_florist` - sotuv vaqtida oformleniya qilgan florist.

Sotuvda yuborilgan `materials[].quantity` ham 1 dona sotuv uchun hisoblanadi. Backend `materials[].quantity * quantity` qilib material skladidan kamaytiradi.

Payload namunasi:

```json
{
  "quantity": 2,
  "sale_price": "1200000",
  "payment_type": "cash",
  "materials": [{"packaging": 3, "quantity": 1}],
  "decoration_florist": 8
}
```

Response katalog obyektini qaytaradi. Sotuv history snapshot ichida:

- `sale_materials`
- `sale_decoration`

maydonlari bo'ladi.

## Florist Detail / Stats

`GET /api/florists/{id}/stats/`

Endi `summary.catalog_count`, `bouquet_count`, `basket_count`, `standard_count`, `custom_count`, `by_arrangement[].count`, `by_volume[].count`, `by_day[].count` katalog row soni emas, real yasalgan dona soni bo'yicha chiqadi.

Masalan 1 ta katalog record ichida `quantity_total = 10` bo'lsa, florist statsda `10` dona deb ko'rsatiladi.
