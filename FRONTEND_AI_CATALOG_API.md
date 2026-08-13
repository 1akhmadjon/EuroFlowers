# EuroFlowers AI Catalog API

## Maqsad

AI mijozlarga hozirgi ichki CRM katalogdan ma'lumot olmaydi. AI uchun alohida katalog page ishlatiladi.

Ichki `/api/catalog/` ishlab chiqarish, florist, sklad, sotuv va hisob-kitob uchun qoladi.

Yangi `/api/ai-catalog/` faqat AI mijozlarga ko'rsatadigan mahsulotlar uchun.

## Endpoint

`/api/ai-catalog/`

CRUD:

- `GET /api/ai-catalog/`
- `POST /api/ai-catalog/`
- `GET /api/ai-catalog/{id}/`
- `PATCH /api/ai-catalog/{id}/`
- `DELETE /api/ai-catalog/{id}/`

Permission:

- `catalog` page permission kerak.
- Admin, operator va content user yozishi mumkin.
- Florist/shogird bu page ko'rmaydi.

## Fieldlar

```json
{
  "id": 1,
  "name": "Gortenziya Mix savat",
  "arrangement_type": "basket",
  "quantity": 1,
  "volume": "M",
  "price": "850000.00",
  "note": "Oq va moviy gortenziyadan yasalgan premium savat.",
  "image_url": "https://...",
  "instagram_link": "https://www.instagram.com/p/...",
  "is_active": true,
  "created_by": 1,
  "created_at": "2026-08-13T...",
  "updated_at": "2026-08-13T..."
}
```

## Field Izohi

- `name` majburiy.
- `arrangement_type` qiymatlari: `bouquet`, `basket`, `box`, `other`.
- `quantity` mijozga ko'rsatish uchun nechta borligi. `0` bo'lsa AI ko'rmaydi.
- `volume` hajmi: masalan `S`, `M`, `L`, `small`, `medium`, `large` yoki erkin matn.
- `price` majburiy.
- `note` AI mijozga tushuntirish berishi uchun izoh.
- `image_url` rasm URL. Rasm bo'lsa AI image/album yubora oladi.
- `instagram_link` optional. Story/post/reel link bilan moslash uchun ishlatiladi.
- `is_active=false` bo'lsa AI ko'rmaydi.

## List Filterlar

`GET /api/ai-catalog/?is_active=true`

`GET /api/ai-catalog/?arrangement_type=basket`

`GET /api/ai-catalog/?search=gortenziya`

`GET /api/ai-catalog/?ordering=-created_at`

Pagination response odatdagi formatda keladi:

```json
{
  "count": 20,
  "page": 1,
  "page_size": 20,
  "total_pages": 1,
  "results": [],
  "totals": {
    "items": 20,
    "quantity_total": 25,
    "value_total": "12500000.00",
    "active": 18,
    "inactive": 2,
    "by_arrangement_type": {
      "bouquet": 10,
      "basket": 8
    }
  }
}
```

## AI Ishlash Qoidasi

AI `get_catalog`, `send_catalog_image`, `send_catalog_album` toollarida faqat `/api/ai-catalog/`dagi faol va quantity > 0 mahsulotlardan foydalanadi.

`/api/catalog/`dagi mahsulotlar AI javobida chiqmaydi.

## Frontend Page

Admin panelda alohida page qiling:

`AI Katalog`

Form fieldlar:

- Nomi
- Turi
- Soni
- Hajmi
- Narxi
- Izoh
- Rasm URL
- Instagram link
- Active switch

Listda ko'rsating:

- Rasm preview
- Nomi
- Turi
- Hajmi
- Soni
- Narxi
- Active status
- Instagram link bor/yo'q
- Edit/Delete

## Muhim

Ichki katalog va AI katalog bir-biriga aralashmasin.

Ichki katalog sotildi, skladdan kamaydi, florist haqi kabi biznes logika uchun.

AI katalog faqat mijozga ko'rsatish va AI javoblari uchun.
