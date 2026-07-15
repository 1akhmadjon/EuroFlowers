# EuroFlowers OS

EuroFlowers premium gul do‘koni uchun filial, sklad, kunlik katalog, Instagram AI va CRM ekotizimi.

## Lokal ishga tushirish

```bash
cp .env.example .env
docker compose up --build
```

Backend API: `http://localhost:8000/api`

Django admin: `http://localhost:8000/admin`

Admin login `.env` ichidagi `ADMIN_USERNAME` orqali yaratiladi.

Admin parol `.env` ichidagi `ADMIN_PASSWORD` orqali yaratiladi.

Demo/mock ma’lumot kerak bo‘lsa:

```bash
docker compose exec backend python manage.py seed_data
```

## Docker ishlatmasdan

```bash
cd backend
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py create_admin
../.venv/bin/python manage.py runserver
```

## Instagram webhook

Callback URL:

```text
https://YOUR-DOMAIN/api/instagram/webhook/
```

Meta panelidagi verify token `.env` ichidagi `INSTAGRAM_VERIFY_TOKEN` bilan bir xil bo‘lishi kerak. Test user Instagram akkaunti Meta ilovasiga qo‘shiladi. `INSTAGRAM_ACCESS_TOKEN` va `INSTAGRAM_ACCOUNT_ID` berilgandan keyin xabarlar `graph.instagram.com` orqali jo‘natiladi.

## Muhim sozlamalar

- `OPENAI_API_KEY`: GPT-5-mini ulanishi
- `OPENAI_MODEL`: standart qiymat `gpt-5-mini`
- `INSTAGRAM_ACCESS_TOKEN`: Instagram test user tokeni
- `INSTAGRAM_ACCOUNT_ID`: professional Instagram account ID
- `INSTAGRAM_VERIFY_TOKEN`: webhook verification tokeni
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_EMAIL`: serverda admin yaratish uchun
- `ALLOWED_HOSTS`: `localhost`, `127.0.0.1`, PC lokal IP va `backend`
- `CORS_ALLOWED_ORIGINS`: `http://localhost:3000`, `http://localhost:5173`, `http://127.0.0.1:3000`, `http://127.0.0.1:5173`

Seed narxlari va rasmlari mock hisoblanadi. Ishga tushirishdan oldin EuroFlowers haqiqiy assortiment va narxlari bilan almashtiriladi.

## Docker joy tozalash

Server SSD to‘lib ketmasligi uchun eski build cache va ishlatilmayotgan image/volume’larni vaqti-vaqti bilan tozalash:

```bash
docker builder prune -f
docker image prune -f
docker container prune -f
```

Volume tozalash faqat database/media kerak emasligiga ishonch bo‘lsa:

```bash
docker volume prune -f
```
