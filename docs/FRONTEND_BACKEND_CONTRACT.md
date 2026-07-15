# EuroFlowers Backend Contract For Frontend

Backend base URL local:

```text
http://192.168.1.5:8000
```

Swagger:

```text
/api/docs/
```

## Auth And Permissions

Login:

```http
POST /api/auth/token/
```

Response includes:

```json
{
  "access": "...",
  "refresh": "...",
  "user": {
    "id": 4,
    "username": "developer",
    "profile": {
      "role": "developer",
      "branches": []
    },
    "permissions": [
      {"page": "dashboard", "can_view": true, "can_control": true}
    ]
  },
  "permissions": [
    {"page": "dashboard", "can_view": true, "can_control": true}
  ]
}
```

Pages:

```text
dashboard, inventory, catalog, crm, customers, conversations, social_posts,
notifications, settings, ai_settings, integrations, users, mini_app, audit
```

Rules:

- `can_view`: page can be opened.
- `can_control`: create/edit/delete/action buttons enabled.
- `developer`: full access, can see AI settings and integrations.
- `admin`: full business access, but should not see developer users.

Users:

```http
GET /api/users/
POST /api/users/
PATCH /api/users/{id}/
GET /api/permissions/
POST /api/permissions/
PATCH /api/permissions/{id}/
```

User create/edit accepts `permissions`:

```json
{
  "username": "operator1",
  "password": "StrongPass123",
  "first_name": "Operator",
  "profile": {"role": "operator", "language": "uz", "branch_ids": [1]},
  "permissions": [
    {"page": "crm", "can_view": true, "can_control": true},
    {"page": "inventory", "can_view": true, "can_control": false}
  ]
}
```

## Settings

Business settings:

```http
GET /api/settings/
PATCH /api/settings/
```

Used for florist fee, AI handoff rules, min-sale wording, working hours.

AI settings, developer only:

```http
GET /api/ai/settings/
PATCH /api/ai/settings/
```

Important fields:

```text
openai_model
system_prompt
temperature
is_active
```

Integrations, developer only:

```http
GET /api/integrations/
PATCH /api/integrations/
```

Important fields:

```text
instagram_access_token
instagram_account_id
instagram_business_id
instagram_verify_token
telegram_bot_token
extra
```

Instagram status:

```http
GET /api/instagram/status/
PATCH /api/instagram/status/
```

PATCH is auth-required. Toggles:

```text
auto_reply_dm
auto_reply_post_reply
auto_reply_story_reply
```

## Uploads

```http
POST /api/uploads/
Content-Type: multipart/form-data
```

Body:

```text
file=<binary>
```

Response:

```json
{"url": "http://.../media/uploads/file.jpg", "path": "uploads/file.jpg"}
```

## Instagram Story/Post/Reel Linking

The frontend should let content users create `SocialPost` records from Instagram links. Backend resolves real Instagram IDs automatically where possible.

Endpoint:

```http
GET /api/social-posts/
POST /api/social-posts/
PATCH /api/social-posts/{id}/
DELETE /api/social-posts/{id}/
```

Main fields:

```text
branch
post_type: post | reel | story | ad
media_id
permalink
instagram_username
story_share_id
webhook_story_id
webhook_story_url
title_uz
title_ru
description_uz
description_ru
price
flower_count
image_url
is_targeted
is_active
```

Frontend create form should allow:

- Instagram link paste.
- Title UZ/RU.
- Description UZ/RU.
- Price.
- Flower count.
- Target enabled toggle.
- Optional image URL/upload.
- Branch.

### Story

User enters public story share link:

```text
https://www.instagram.com/stories/extra_teest/3941678488959215749?...
```

Backend stores:

```text
post_type = story
media_id = story-share-3941678488959215749
instagram_username = extra_teest
story_share_id = 3941678488959215749
```

Backend calls Instagram active stories API and, if story is still active, fills:

```text
webhook_story_id = 18101433071220523
webhook_story_url = ...
```

Webhook matching:

- Story reply payload uses `message.reply_to.story.id`.
- Story direct-send payload uses `attachments[0].payload.story_media_id`.
- Both match by `SocialPost.webhook_story_id`.

Real tested payload fields:

```text
story reply:
message.reply_to.story.id = 18101433071220523

story send:
attachments[0].type = ig_story
attachments[0].payload.story_media_id = 18101433071220523
```

### Post

User enters post link:

```text
https://www.instagram.com/p/DYfK5HhDSKM/?utm_source=ig_web_copy_link
```

Backend normalizes and calls Instagram `/media`, then stores:

```text
post_type = post
media_id = 18448508641115058
permalink = https://www.instagram.com/p/DYfK5HhDSKM/
```

Webhook matching:

```text
attachments[0].type = ig_post
attachments[0].payload.ig_post_media_id = 18448508641115058
```

Matches by:

```text
SocialPost.media_id == ig_post_media_id
```

### Reel

User enters reel link:

```text
https://www.instagram.com/reel/Daz5sXFNz23/?igsh=...
```

Backend normalizes and calls Instagram `/media`, then stores:

```text
post_type = reel
media_id = 18067602464713804
permalink = https://www.instagram.com/reel/Daz5sXFNz23/
```

Webhook matching:

```text
attachments[0].type = ig_reel
attachments[0].payload.reel_video_id = 18067602464713804
```

Matches by:

```text
SocialPost.media_id == reel_video_id
```

### Duplicate Handling

If the same media/link already exists:

```http
400 Bad Request
```

Examples:

```json
{"media_id": "Bu Instagram media allaqachon SocialPost id=9 da bor."}
```

```json
{"permalink": "Bu Instagram link allaqachon SocialPost id=11 da bor."}
```

Frontend should show this as a normal validation error, not a crash.

### Fallback Behavior

Sometimes Instagram `/media` may not return a newly uploaded reel immediately. Backend then stores fallback:

```text
media_id = post-link-{shortcode}
```

When a webhook later arrives with real media ID, backend upgrades the existing `SocialPost.media_id` automatically if permalink matches.

## AI Context For Instagram Media

When a customer sends or replies to a story/post/reel, backend links the conversation to `Conversation.social_post`.

AI receives this post context:

```text
title_uz
title_ru
description_uz
description_ru
price
flower_count
```

AI also receives stock, catalog, basket, florist fee, and business rules context.

Important frontend requirement: content managers must fill post/reel/story title, description, price, flower count, and ideally link it to catalog data. Instagram link alone only identifies the media; it does not contain EuroFlowers product price/composition.

## Instagram Events Debug Page

Frontend can build a developer/debug table from:

```http
GET /api/instagram/events/
```

Useful fields:

```text
event_type
sender_id
recipient_id
message_id
text
media_id
story_id
story_url
extracted
raw_payload
created_at
```

Event types:

```text
message
story_reply
story_send
media_send
```

## Mini App API

Mini app uses Telegram `init_data`. If `telegram_bot_token` is configured, backend validates the hash.

Catalog:

```http
GET /api/mini-app/catalog/?init_data=...&branch=1
```

Response:

```json
{
  "catalog": [],
  "stock": [],
  "packaging": []
}
```

Quote:

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
    {"stock_batch": 1, "quantity_stems": 25}
  ],
  "packaging": 2
}
```

Response:

```json
{
  "lines": [],
  "packaging": {},
  "florist_fee": "50000.00",
  "estimated_price": "850000.00",
  "price_is_estimate": true
}
```

Lead:

```http
POST /api/mini-app/leads/
```

Body:

```json
{
  "init_data": "...",
  "branch": 1,
  "arrangement_type": "bouquet",
  "items": [
    {"catalog_item": 3, "quantity": 1}
  ],
  "name": "Ali",
  "phone": "+998901234567",
  "note": "Bugun kerak"
}
```

Creates:

- `Customer` with `instagram_user_id = miniapp:{telegram_user_id}`.
- `Lead` with `source = mini_app`.
- Lead notification.

## Other Implemented Backend Needs

Notifications:

```http
POST /api/notifications/{id}/read/
POST /api/notifications/read_all/
```

Stock batch delete:

- Protected batches with movements no longer crash as 500.
- Frontend should prefer `PATCH {"is_active": false}` for deactivate UX.

Date filters exist on main list endpoints where needed:

```text
created_at_after
created_at_before
```

Dashboard includes revenue/order/conversion fields.

Customers include:

```text
leads_count
purchases_count
total_spent
```

Token blacklist:

```http
POST /api/auth/token/blacklist/
```

## Current Local Test Data

Keep these in local DB for development only:

```text
SocialPost id=3  story  webhook_story_id=18101433071220523
SocialPost id=9  post   media_id=18448508641115058
SocialPost id=11 reel   media_id=18067602464713804
```

These are test records for local verification. Production will use the real EuroFlowers Instagram account and real flower media.
