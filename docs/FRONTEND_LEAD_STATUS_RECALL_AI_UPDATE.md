# Frontend Update: Lead Status, Recall, AI Session

API base:

```text
https://euroflowers.api.cognilabs.org/api
```

WebSocket:

```text
wss://euroflowers.api.cognilabs.org/ws/notifications/?token=ACCESS_TOKEN
```

## 1. Dynamic Lead Statuslari

Lead statuslari endi backenddan boshqariladi. Frontend kanban columnlarini hardcode qilmasligi kerak.

Endpointlar:

```http
GET /api/lead-statuses/?is_active=true&ordering=order
POST /api/lead-statuses/
PATCH /api/lead-statuses/{id}/
DELETE /api/lead-statuses/{id}/
```

Response fieldlari:

```json
{
  "id": 1,
  "key": "new",
  "name_uz": "Yangi",
  "name_ru": "Новый",
  "color": "#2563eb",
  "order": 10,
  "is_active": true,
  "created_at": "2026-07-21T20:00:00+05:00",
  "updated_at": "2026-07-21T20:00:00+05:00"
}
```

Default statuslar:

```text
new
qualified
contacted
won
lost
```

Frontend:

- CRM kanban columnlarini `/api/lead-statuses/?is_active=true&ordering=order` dan chizadi.
- Lead drag/drop status update qilganda `PATCH /api/leads/{id}/` bodyda `status` string yuboradi.
- Rang uchun `color` string ishlatiladi.
- Admin/operator status create/edit qila oladi.

## 2. Lead Response O‘zgarishi

`Lead.status` string bo‘lib qoladi. Response ichida `status_detail` ham keladi.

```json
{
  "id": 25,
  "status": "new",
  "status_detail": {
    "id": 1,
    "key": "new",
    "name_uz": "Yangi",
    "name_ru": "Новый",
    "color": "#2563eb",
    "order": 10,
    "is_active": true
  }
}
```

Noto‘g‘ri status yuborilsa backend `400` qaytaradi:

```json
{
  "status": "Bunday lead statusi mavjud emas"
}
```

## 3. Lead Recall

Lead modelga yangi datetime fieldlar qo‘shildi:

```json
{
  "delivery_at": "2026-07-21T18:00:00+05:00",
  "recall_at": "2026-07-21T17:00:00+05:00",
  "recall_sent_at": null
}
```

Qoidalar:

- Frontend `delivery_at` yuborsa va `recall_at` yubormasa, backend avtomatik `delivery_at - 1 hour` qilib saqlaydi.
- Frontend custom recall time kerak bo‘lsa `recall_at`ni o‘zi yuboradi.
- `recall_sent_at` read-only sifatida ko‘rsatiladi.
- `lost` statusdagi leadlarga recall yuborilmaydi.

Lead create/update example:

```json
{
  "customer": 14,
  "branch": 1,
  "status": "new",
  "request_uz": "Qizil atirgul buketi",
  "arrangement_type": "catalog",
  "delivery_at": "2026-07-22T19:00:00+05:00"
}
```

## 4. Recall Notification

Recall vaqti kelganda backend:

- `Notification` yaratadi;
- WS orqali `notification.created` yuboradi;
- Telegram group chat id sozlangan bo‘lsa bot orqali groupga xabar yuboradi.

WS event:

```json
{
  "type": "notification.created",
  "notification": {
    "notification_type": "lead",
    "title_uz": "Recall: Lead #25",
    "body_uz": "Ahmad buyurtmasi 1 soat ichida yuborilishi kerak..."
  }
}
```

Frontend notification bell/list shu event kelganda refetch qilishi yoki eventdagi notificationni listga qo‘shishi kerak.

## 5. Telegram Group Sozlamasi

Integrations endpointga yangi field qo‘shildi:

```http
GET /api/integrations/
PATCH /api/integrations/
```

Field:

```json
{
  "telegram_group_chat_id": "-1001234567890"
}
```

Bu field bo‘sh bo‘lsa backend `.env` dagi `TELEGRAM_GROUP_CHAT_ID` ni fallback sifatida ishlatadi.

## 6. Celery Beat

Backendda yangi service bor:

```text
beat
```

U har 60 sekundda recall muddati kelgan leadlarni tekshiradi. Frontend uchun alohida action kerak emas.

Deployda quyidagilar running bo‘lishi kerak:

```text
backend
worker
beat
redis
db
```

## 7. AI 24 Soat Session Reset

Mijoz oxirgi xabardan 24 soatdan keyin yozsa, AI suhbatni yangi session deb ko‘radi.

Natija:

- AI salomlashuvdan boshlaydi.
- Oldingi savollarni mijoz eslatmasa o‘zi eslatmaydi.
- Eski lead/order ma’lumotlarini faqat mijoz o‘zi so‘rasa ishlatadi.
- Eski lead avtomatik yangi lead qilib yaratilmaydi.

Frontend uchun o‘zgarish kerak emas, bu backend AI context ichida ishlaydi.

## 8. Permission

`/api/lead-statuses/` CRM permission bilan ishlaydi.

Expected:

- `crm.can_view=true` bo‘lsa list/detail ko‘radi.
- `crm.can_control=true` va role `admin/operator` bo‘lsa create/edit/delete qila oladi.

## 9. Frontend Checklist

- CRM kanban status columnlarini hardcode qilishni to‘xtatish.
- `/api/lead-statuses/?is_active=true&ordering=order` dan statuslarni olish.
- Lead cardlarda `status_detail.color` ishlatish.
- Lead formga `delivery_at` field qo‘shish.
- Kerak bo‘lsa advanced field sifatida `recall_at` qo‘shish.
- Notification WS event kelganda bell/listni realtime yangilash.
- Integrations settings sahifasiga `telegram_group_chat_id` input qo‘shish.
