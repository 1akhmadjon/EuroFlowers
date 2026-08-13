# EuroFlowers Florist Account

## Maqsad

Florist va shogird accountlarida global CRM ko'rinmaydi. Bu accountlar faqat o'z ishini ko'radi: shaxsiy dashboard, davomat, kunlik/oylik ish haqi va o'ziga tegishli notificationlar.

## Role

Backend role qiymatlari:

- `florist`
- `apprentice`

Frontend `/api/me/` dan user role va `permissions` oladi.

Florist/shogird uchun backend faqat shu permission pagelarni qaytaradi:

- `florists`
- `attendance`
- `notifications`

Frontend florist/shogird role ko'rsa, boshqa menyularni umuman render qilmasin.

## Ko'rinadigan Sahifalar

### 1. Shaxsiy Dashboard

Endpoint:

`GET /api/florists/me/dashboard/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`

Ishlatish:

- Bugungi ish haqi
- Tanlangan sana oralig'idagi jami ish haqi
- Qaysi kuni qancha pul yozilgani
- Qaysi hajmdagi buket/savatdan nechta qilingani
- Davomat tarixi

Florist dashboardida sotuv summasi, foyda, karta/naqd, katalog sotildi statistikasi ko'rsatilmaydi.

Response ichida frontend ishlatadigan asosiy joylar:

- `florist`
- `period`
- `summary.salary_total`
- `summary.catalog_count`
- `summary.catalog_salary_total`
- `summary.decoration_salary_total`
- `summary.daily_salary_total`
- `summary.manual_salary_total`
- `by_day`
- `by_source`
- `by_volume`
- `salary_entries`
- `attendance`

### 2. Davomat

Endpointlar:

- `GET /api/florist-attendance/`
- `POST /api/florist-attendance/check-in/`
- `POST /api/florist-attendance/check-out/`

Florist faqat o'z davomatini ko'radi.

Mobile app location orqali `check-in` va `check-out` yuboradi. Web frontendda faqat tarix va status ko'rsatilsa yetarli.

### 3. Ish Haqi Tarixi

Endpoint:

`GET /api/florist-salary/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`

Florist faqat o'z salary entrylarini ko'radi.

Ko'rsatish:

- Sana
- Summa
- Sabab turi
- Izoh

Ko'rsatmaslik:

- Katalog sotuv narxi
- Katalog foydasi
- Karta/naqd
- Qaysi mijoz sotib olgani

### 4. Notification

Endpointlar:

- `GET /api/notifications/?is_read=false`
- `POST /api/notifications/{id}/mark-read/`
- `POST /api/notifications/read_all/`

Floristga ko'rinadigan notificationlar:

- Ish haqi qo'shildi
- Davomat belgilandi
- Yangi ish biriktirildi

Floristga ko'rinmaydigan notificationlar:

- Katalog sotildi
- Katalog chiqitga chiqdi
- Admin/global lead notificationlari
- Developerga oid har qanday notification

## Ko'rinmasligi Shart Bo'lgan Sahifalar

Florist/shogird accountda quyidagilar umuman bo'lmasin:

- Global dashboard
- Katalog page
- Hisob-kitob page
- Filial hisoboti
- Analytics
- CRM/leads/customers
- Conversations/chatlar
- Sklad
- Material sklad
- Postavshiklar
- Floristlar ro'yxati
- Users/team
- Audit log
- Settings
- AI settings
- Integrations

Backend bu endpointlarga florist role uchun `403` qaytaradi, frontend baribir ularni chaqirmasligi kerak.

## Frontend Route Tavsiya

Florist login bo'lganda asosiy layout alohida bo'lsin:

- `/florist/dashboard`
- `/florist/attendance`
- `/florist/salary`
- `/florist/notifications`
- `/profile`

Admin layout bilan aralashtirmang. Florist role aniqlansa admin sidebar ishlatilmasin.

## UI Tavsiya

Dashboardda oddiy va tushunarli kartalar bo'lsin:

- Bugun yozilgan summa
- Shu oy jami
- Yasagan ishlar soni
- Davomat statusi

Pastda:

- Kunlar bo'yicha jadval
- Salary entrylar listi
- Davomat tarixi

Florist uchun pul va davomat asosiy narsa. Katalog sotuv, foyda, mijoz, CRM terminlarini ishlatmang.

## Qabul Mezoni

- Florist login qilganda admin dashboard ko'rinmaydi.
- Florist katalog/hisob-kitob/analytics sahifalariga URL bilan kirsa ham frontend ko'rsatmaydi, backend `403` qaytaradi.
- Florist notificationda katalog sotildi degan xabar ko'rmaydi.
- Florist faqat o'z salary va davomatini ko'radi.
- Admin va developer accountlarda mavjud sahifalar buzilmaydi.
