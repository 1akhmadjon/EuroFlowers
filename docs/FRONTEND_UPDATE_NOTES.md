# Frontend Update Notes

## New Developer Role

Backend now has a new role:

```text
developer
```

Developer has higher access than admin.

Local developer login after demo seed:

```text
username: developer
password: SEED_PASSWORD from .env
```

Admin users must not see developer users in the team/users page. Developer can see everyone.

## Auth Login Response Changed

`POST /api/auth/token/` now returns:

```json
{
  "refresh": "...",
  "access": "...",
  "user": {
    "id": 1,
    "username": "developer",
    "first_name": "Dev",
    "last_name": "EuroFlowers",
    "email": "dev@euroflowers.uz",
    "is_active": true,
    "profile": {
      "role": "developer",
      "language": "uz",
      "branches": []
    },
    "permissions": []
  },
  "permissions": [
    {
      "page": "dashboard",
      "label": "Dashboard",
      "can_view": true,
      "can_control": true
    }
  ]
}
```

Use `permissions` to show or hide pages and controls.

## Permission Pages

Available permission pages:

```text
dashboard
inventory
catalog
crm
customers
conversations
social_posts
notifications
settings
ai_settings
integrations
users
mini_app
audit
```

Each page has:

```json
{
  "page": "inventory",
  "can_view": true,
  "can_control": false
}
```

- `can_view`: user can open/read page
- `can_control`: user can create/edit/delete/use actions

## Permissions API

```http
GET /api/permissions/
POST /api/permissions/
PATCH /api/permissions/{id}/
DELETE /api/permissions/{id}/
```

Requires `users` page permission.

User create/edit now accepts permissions:

```json
{
  "username": "manager1",
  "password": "secret123",
  "first_name": "Manager",
  "role": "operator",
  "branch_ids": [1],
  "permissions": [
    {"page": "dashboard", "can_view": true, "can_control": false},
    {"page": "inventory", "can_view": true, "can_control": true}
  ]
}
```

## AI Settings

Developer-only.

```http
GET /api/ai/settings/
PATCH /api/ai/settings/
```

Fields:

```json
{
  "openai_model": "gpt-5-mini",
  "system_prompt": "...",
  "temperature": "0.20",
  "is_active": true
}
```

Use this page only for `developer`.

## Integrations Settings

Developer-only.

```http
GET /api/integrations/
PATCH /api/integrations/
```

Fields:

```json
{
  "instagram_access_token": "",
  "instagram_account_id": "",
  "instagram_business_id": "",
  "instagram_verify_token": "",
  "telegram_bot_token": "",
  "extra": {}
}
```

Backend uses DB integration settings first, `.env` as fallback.

## Mini App API

Public endpoints for customer mini app.

### Catalog

```http
GET /api/mini-app/catalog/
```

Optional query:

```text
?branch=1&init_data=...
```

Returns:

```json
{
  "catalog": [],
  "stock": [],
  "packaging": []
}
```

### Quote

```http
POST /api/mini-app/quote/
```

Example bouquet:

```json
{
  "init_data": "",
  "branch": 1,
  "arrangement_type": "bouquet",
  "items": [
    {"stock_batch": 1, "quantity_stems": 11}
  ]
}
```

Example catalog item:

```json
{
  "arrangement_type": "catalog",
  "items": [
    {"catalog_item": 1, "quantity": 1}
  ]
}
```

Response:

```json
{
  "lines": [],
  "packaging": null,
  "florist_fee": "50000.00",
  "estimated_price": "435000.00",
  "price_is_estimate": true
}
```

### Create Lead

```http
POST /api/mini-app/leads/
```

Example:

```json
{
  "init_data": "",
  "branch": 1,
  "arrangement_type": "bouquet",
  "items": [
    {"stock_batch": 1, "quantity_stems": 11}
  ],
  "name": "Ali",
  "phone": "+998901234567",
  "note": "Bugun kechga kerak"
}
```

Creates CRM lead with:

```text
source = mini_app
```

## Mini App Init Data

`init_data` supports Telegram WebApp init data.

If `telegram_bot_token` exists in `/api/integrations/`, backend validates HMAC.
If token is empty, backend accepts requests in dev/mock mode.

## Frontend Permission Rules

Use this logic:

```ts
const canView = permissions.find((p) => p.page === page)?.can_view
const canControl = permissions.find((p) => p.page === page)?.can_control
```

Hide page if `can_view=false`.

Disable or hide create/edit/delete/action buttons if `can_control=false`.

Developer should see developer-only pages:

- AI Settings
- Integrations

Admin should not see:

- developer users
- AI Settings
- Integrations
