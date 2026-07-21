# Frontend uchun oxirgi backend o‘zgarishlar

## Deploy

Backend productionga deploy qilingan.

API base:

```text
https://euroflowers.api.cognilabs.org/api
```

WebSocket:

```text
wss://euroflowers.api.cognilabs.org/ws/notifications/?token=ACCESS_TOKEN
```

## Realtime CRM/chat eventlari

Shu notification WS kanali endi notificationdan tashqari CRM va chat eventlarini ham yuboradi.

Frontend bitta websocket ulanishni ochib, `type` bo‘yicha listlarni yangilashi kerak.

### `conversation.created`

Yangi Instagram/Telegram chat ochilganda keladi.

```json
{
  "type": "conversation.created",
  "conversation": {}
}
```

Frontend:

- inbox/chat listni refetch qilish;
- yoki `conversation`ni list boshiga qo‘shish.

### `message.created`

Chatga mijoz, AI yoki operator xabari qo‘shilganda keladi.

```json
{
  "type": "message.created",
  "conversation_id": 12,
  "message": {}
}
```

Frontend:

- shu `conversation_id` detail ochiq bo‘lsa message’ni qo‘shish;
- chat listda last message/time’ni yangilash;
- kerak bo‘lsa inbox listni refetch qilish.

### `lead.created`

CRMga yangi lead tushganda keladi.

```json
{
  "type": "lead.created",
  "lead": {}
}
```

Frontend:

- CRM kanban/listni refetch qilish;
- dashboard counterlarini refetch qilish.

### `lead.updated`

Lead statusi yoki ma’lumotlari o‘zgarganda keladi.

```json
{
  "type": "lead.updated",
  "lead": {}
}
```

Frontend:

- lead kartani yangilash;
- status `won/lost/...` bo‘lsa dashboardni refetch qilish.

### `notification.created`

Oldingi notification event saqlanib qolgan.

```json
{
  "type": "notification.created",
  "notification": {}
}
```

Eventlar user permission va filialga qarab yuboriladi:

- `conversation.created` / `message.created` -> `conversations` view permission;
- `lead.created` / `lead.updated` -> `crm` view permission;
- `notification.created` -> `notifications` view permission.

## Lead statuslari

Lead statuslari endi backendda dynamic.

Endpoint:

```http
GET /api/lead-statuses/
POST /api/lead-statuses/
PATCH /api/lead-statuses/{id}/
DELETE /api/lead-statuses/{id}/
```

Fieldlar:

```json
{
  "id": 1,
  "key": "new",
  "name_uz": "Yangi",
  "name_ru": "Новый",
  "color": "#2563eb",
  "order": 10,
  "is_active": true
}
```

Default status keys:

```text
new
qualified
contacted
won
lost
```

`Lead.status` string bo‘lib qoladi. Lead response ichida `status_detail` ham keladi:

```json
{
  "status": "new",
  "status_detail": {
    "key": "new",
    "name_uz": "Yangi",
    "color": "#2563eb"
  }
}
```

CRM kanban columnlarini `/api/lead-statuses/?is_active=true&ordering=order` orqali olish kerak.

## Lead recall

Leadga yuborish vaqti qo‘shildi:

```json
{
  "delivery_at": "2026-07-21T18:00:00+05:00",
  "recall_at": "2026-07-21T17:00:00+05:00",
  "recall_sent_at": null
}
```

`delivery_at` yuborilsa, `recall_at` bodyda kelmasa backend avtomatik `delivery_at - 1 hour` qiladi.

Recall vaqti kelganda backend:

- `Notification` yaratadi;
- WS orqali `notification.created` yuboradi;
- Telegram group chat id sozlangan bo‘lsa bot orqali groupga xabar yuboradi.

Telegram group sozlamasi:

```http
GET/PATCH /api/integrations/
```

Field:

```json
{
  "telegram_group_chat_id": "-1001234567890"
}
```

Env fallback:

```text
TELEGRAM_GROUP_CHAT_ID=-1001234567890
```

## AI 24 soat reset

Mijoz oxirgi xabardan 24 soatdan keyin yozsa, AI suhbatni yangi session deb ko‘radi:

- salomlashuvdan boshlaydi;
- oldingi savollarni o‘zi eslatmaydi;
- oldingi lead/zakazlarni faqat mijoz o‘zi so‘rasa aytadi.

AI contextda `recent_orders` bor, lekin faqat ma’lumot uchun ishlatiladi. Eski lead avtomatik yangi lead qilib yaratilmaydi.

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
