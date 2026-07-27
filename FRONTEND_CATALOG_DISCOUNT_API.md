# Catalog Discount va History API

Bu hujjat EuroFlowers CRM frontend uchun katalog yaratish, custom katalog, sotuvdagi skidka va history fieldlarini ulash bo‘yicha.

## Catalog model fieldlari

`GET /api/catalog/` va `GET /api/catalog/{id}/` javobida asosiy yangi fieldlar:

```json
{
  "volume": "small yoki extra katta 120 dona",
  "florist_fee": "80000.00",
  "florist_salary_amount": "125000.00",
  "calculated_component_price": "380000.00",
  "discount_amount": "0.00",
  "discount_percent": "0.00",
  "discount_reason": "",
  "history": []
}
```

`volume` endi faqat `small`, `medium`, `large` emas. Frontend erkin text yuborishi mumkin.

`florist_fee` mijozdan olinadigan florist haqi va foyda hisobiga kiradi.

`florist_salary_amount` florist oyligiga qo‘shiladigan summa. Custom katalogda florist oyligi faqat shu fielddan hisoblanadi.

## Custom Catalog Create

Endpoint:

`POST /api/catalog/`

Custom katalog doim auto `sold` bo‘ladi va historyga `created` hamda `sold` event yoziladi.

Request:

```json
{
  "name_uz": "Juda katta custom buket",
  "arrangement_type": "bouquet",
  "catalog_kind": "custom",
  "volume": "extra katta 120 dona",
  "florist": 12,
  "price": "450000.00",
  "florist_fee": "80000.00",
  "florist_salary_amount": "125000.00",
  "discount_reason": "Doimiy mijozga chegirma",
  "quantity_total": 1,
  "composition": [
    {
      "stock_batch": 18,
      "quantity_stems": 10,
      "quantity_bunches": "0.50"
    }
  ],
  "materials": [
    {
      "packaging": 5,
      "quantity": 1
    }
  ]
}
```

Agar `price` calculated component narxdan arzon bo‘lsa, `discount_reason` majburiy.

`calculated_component_price` ichiga gul sotuv narxi, material sotuv narxi va `florist_fee` kiradi.

## Standard Catalog Create

Standart katalogda ham `volume` erkin text bo‘lishi mumkin:

```json
{
  "name_uz": "Premium katta savat",
  "arrangement_type": "basket",
  "catalog_kind": "standard",
  "volume": "large plus 90 dona",
  "florist": 12,
  "price": "1200000.00",
  "florist_fee": "100000.00",
  "florist_salary_amount": "85000.00",
  "quantity_total": 3,
  "composition": [],
  "materials": []
}
```

Standart katalogda `florist_salary_amount` yuborilsa shu summa florist salaryga yoziladi. Yuborilmasa, `volume` bo‘yicha saqlangan rate topilsa auto qo‘yiladi.

## Catalog Sell

Endpoint:

`POST /api/catalog/{id}/sell/`

Oddiy sotuv:

```json
{
  "quantity": 1
}
```

Skidka bilan sotuv:

```json
{
  "quantity": 1,
  "sale_price": "450000.00",
  "discount_reason": "VIP mijoz"
}
```

`sale_price` katalogdagi `price`dan past bo‘lsa, `discount_reason` majburiy.

Response katalog detail qaytaradi. `history` ichida yangi `sold` event bo‘ladi.

## Catalog History

`history` item ichida qaytadi:

```json
{
  "id": 44,
  "action": "sold",
  "quantity": 1,
  "listed_unit_price": "500000.00",
  "sold_unit_price": "450000.00",
  "discount_amount": "50000.00",
  "discount_percent": "10.00",
  "discount_reason": "VIP mijoz",
  "note": "",
  "snapshot": {
    "catalog": "API skidka buket",
    "catalog_kind": "standard",
    "arrangement_type": "bouquet",
    "volume": "large",
    "composition": [],
    "materials": []
  },
  "created_by_detail": {},
  "created_at": "2026-07-27T18:00:00+05:00"
}
```

Frontend detail page’da history timeline ko‘rsatishi kerak:

`created` katalog qachon va kim tomonidan qo‘shilgan.

`sold` qachon, kim, necha dona, qaysi narxda sotgan, skidka sababini ko‘rsatadi.

## Dashboard Fieldlari

`GET /api/dashboard/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`

Yangi fieldlar:

```json
{
  "discounted_catalog_sales_count": 3,
  "discounted_catalog_quantity": 5,
  "discounted_catalog_amount": "250000.00"
}
```

## Analytics Fieldlari

`GET /api/analytics/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`

`summary` ichida:

```json
{
  "discounted_catalog_sales_count": 3,
  "discounted_catalog_quantity": 5,
  "discounted_catalog_amount": "250000.00"
}
```

Frontend dashboardda alohida cardlar tavsiya qilinadi:

`Skidka bilan sotuvlar`

`Skidkada sotilgan dona`

`Umumiy skidka summasi`

## Frontend Validatsiya

Custom catalog create:

`price < calculated_component_price` bo‘lishi mumkin. Bunday holatda frontend `discount_reason` so‘rashi kerak.

Standard sell:

`sale_price < price` bo‘lsa, frontend `discount_reason` so‘rashi kerak.

`volume` input select + custom text bo‘lsin:

Default chips: `small`, `medium`, `large`

Custom input: masalan `extra katta 120 dona`
