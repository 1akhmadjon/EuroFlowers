# Frontend Update: Permission, Kanban Order, Unknown Media

API base:

```text
https://euroflowers.api.cognilabs.org/api
```

## 1. Developer-Only Permissionlar

Quyidagi sahifalar faqat `developer` rolida bo‘ladi:

```text
ai_settings
integrations
audit
```

Backend qoidalari:

- Admin bu permissionlarni boshqa userlarga bera olmaydi.
- Admin `/api/permissions/` listida bu permissionlarni ko‘rmaydi.
- Admin `/api/users/{id}/` permission matrix ichida bu sahifalarni ko‘rmaydi.
- Admin `/api/audit/`, `/api/ai/settings/`, `/api/integrations/` endpointlariga kira olmaydi.
- Developer hammasini ko‘radi va boshqara oladi.

Frontend:

- User permission UI’da `ai_settings`, `integrations`, `audit` checkboxlarini faqat current user role `developer` bo‘lsa ko‘rsating.
- Admin panelda bu uch permissionni umuman chiqarmang.
- Backenddan 400/403 qaytsa UI aniq error ko‘rsatsin.

## 2. Lead Kanban Tartibi

Lead modelga yangi field qo‘shildi:

```json
{
  "sort_order": "2000.000000"
}
```

Kanban list olish:

```http
GET /api/leads/?ordering=sort_order
```

Yoki column bo‘yicha:

```http
GET /api/leads/?status=new&ordering=sort_order
```

Frontend:

- Har bir status column ichida leadlarni `sort_order` bo‘yicha chizing.
- Drag/dropdan keyin refresh bo‘lsa ham joy saqlanadi.

## 3. Lead Move Action

Leadni column ichida yoki boshqa statusga ko‘chirish uchun:

```http
POST /api/leads/{id}/move/
```

Body variant 1: ikkita lead orasiga qo‘yish

```json
{
  "status": "new",
  "before": 12,
  "after": 18
}
```

Body variant 2: column boshiga qo‘yish

```json
{
  "status": "new",
  "after": 18
}
```

Body variant 3: column oxiriga qo‘yish

```json
{
  "status": "new",
  "before": 12
}
```

Body variant 4: frontend o‘zi sort_order hisoblab yuborsa

```json
{
  "status": "qualified",
  "sort_order": "2500.000000"
}
```

Response: yangilangan `LeadSerializer`.

```json
{
  "id": 25,
  "status": "qualified",
  "sort_order": "2500.000000",
  "status_detail": {}
}
```

Validation:

- `status` mavjud `LeadStatus.key` bo‘lishi kerak.
- `before` va `after` leadlari shu filial va shu target statusda bo‘lishi kerak.
- Status `won`ga move qilinsa backend lead ichidagi sklad/katalog/material chiqimini bajaradi.

## 4. Unknown Instagram Media

Instagramdan story/post/reel sent yoki reply bo‘lib keldi, lekin backend uni tizimdagi `SocialPost` bilan bog‘lay olmasa:

- conversation eski post/reel/story kontekstini clear qiladi;
- AI eski Pion yoki boshqa oldingi post ma’lumotini ishlatmaydi;
- customer message textida link qoladi;
- message ichida system note bo‘ladi.

CRM chat message example:

```text
shu qancha
Reel link: https://www.instagram.com/reel/UNKNOWN123/
Tizim izohi: yuborilgan Instagram media bazadagi story/post/reel katalogiga bog‘lanmagan.
```

AI bunday holatda boshqa katalogni taxmin qilib aytmasligi kerak. U mijozdan gul nomini aniqlashtiradi yoki ism-raqam qoldirishni so‘raydi.

Frontend uchun:

- `Message.text`ni newline bilan ko‘rsating.
- `Message.metadata.attachments` bo‘lsa link preview yoki clickable link qilib chiqarsa bo‘ladi.

Attachment metadata:

```json
{
  "attachments": [
    {
      "kind": "reel",
      "type": "share",
      "url": "https://www.instagram.com/reel/UNKNOWN123/",
      "source": "instagram_attachment"
    }
  ]
}
```
