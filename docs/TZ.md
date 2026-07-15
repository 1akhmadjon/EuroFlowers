# EuroFlowers ekotizimi texnik topshirig‘i

## 1. Maqsad

Instagram orqali kelgan mijozlarni AI yordamida malakali leadga aylantirish, mijoz tarixini yagona CRM kartasida saqlash, filiallar kesimida gul qoldig‘ini boshqarish va kunlik tayyor kompozitsiyalar katalogini yuritish.

## 2. Foydalanuvchi rollari

| Rol | Vakolat |
|---|---|
| Administrator | Barcha filiallar, xodimlar, sozlamalar va audit |
| Operator | Inbox, mijoz, lead va lead statuslari |
| Florist | Katalog, kompozitsiya tarkibi va sotilgan holati |
| Skladchi | Kirim, chiqim, partiya, narx va inventarizatsiya |
| Kontent menejer | Katalog, post, story, reel va target ma’lumotlari |

Har bir xodim bir yoki bir nechta filialga biriktiriladi. Superadmin barcha filiallarni ko‘radi.

## 3. Sklad

Har bir gul partiyasida filial, gul turi, nav, rang, bo‘y, rasm, kelgan sana, partiya raqami, pochka ichidagi dona, kelgan dona, qolgan dona, tannarx, dona sotuv narxi, pochka sotuv narxi va minimal sotuv miqdori saqlanadi.

Asosiy hisob birligi dona. Pochka miqdori partiyaning `stems_per_bunch` qiymati orqali hisoblanadi. Bir xil navning turli partiyalarida pochka hajmi va narxi har xil bo‘lishi mumkin.

Har bir kirim va chiqim alohida harakat sifatida audit qilinadi. Qoldiq manfiy bo‘lishi taqiqlanadi. Kritik qoldiqda notification yaratiladi.

## 4. Kunlik katalog

Katalog elementi buket, savat yoki quti bo‘ladi. Unda ikki tildagi nom va tavsif, rasm, narx, o‘lcham, Instagram story havolasi, filial va sklad partiyalaridan tuzilgan tarkib bo‘ladi.

Katalogga qo‘shish skladni rezerv qilmaydi va kamaytirmaydi. Florist “Sotildi” qilganda ham qoldiq avtomatik kamaymaydi. Tizim skladchi uchun “Sklad kamaytirilmagan” notification yaratadi. Skladchi tarkibni tekshirib, alohida tasdiq bilan chiqim qiladi. Bir kompozitsiyani ikki marta chiqim qilish taqiqlanadi.

## 5. CRM

Mijoz Instagram scoped user ID bo‘yicha bir marta yaratiladi. Telefon normalizatsiya qilinadi. Bitta mijozda cheklanmagan lead va conversation bo‘lishi mumkin.

Lead tarkibi: mijoz, filial, manba, bog‘langan conversation yoki post, so‘rov, kompozitsiya turi, taxminiy narx, kerakli sana, mas’ul operator va status.

Pipeline: `new`, `qualified`, `contacted`, `won`, `lost`.

## 6. Instagram

Integratsiya Instagram Login va `graph.instagram.com` orqali ishlaydi. Webhook test user yuborgan xabarlarni qabul qiladi. Event ichidagi referral yoki media ID tizimdagi postga bog‘lanadi. Takroriy webhook event `instagram_message_id` orqali qayta ishlanmaydi.

Post bazasida oddiy post, reel, story va target yozuvlari saqlanadi. Har bir yozuv media ID, permalink, rasm, ikki tildagi tavsif, tarkib, narx va filialga ega.

Operator suhbatni AI’dan olishi va keyin AI’ga qaytarishi mumkin. Operator javobi Instagram API orqali mijozga jo‘natiladi.

## 7. AI sotuvchi

Model: `gpt-5-mini`.

Modelga conversation tarixi, mijoz profili, bog‘langan post, filialdagi aktual sklad, kunlik katalog, savatlar va biznes qoidalari bevosita prompt konteksti sifatida beriladi. Alohida dialog triggerlari yoki scripted helper oqimlari ishlatilmaydi.

AI faqat berilgan qoldiq va narxdan foydalanadi. Noaniq gul nomida mavjud nav, rang va bo‘ylarni taklif qiladi. Minimal sotuv sonini tekshiradi.

Buket taxminiy narxi:

`gullar + o‘ram + 50 000 so‘m florist xizmati`

Savat taxminiy narxi:

`gullar + miqdorga mos savat + 50 000 so‘m florist xizmati`

AI narx taxminiy ekanini va yakuniy ma’lumotni operator berishini aytadi. Yetkazib berish va to‘lov bo‘yicha va’da bermaydi.

Birinchi sotuv niyatida ism va telefon olinadi. Qayta murojaatda ism bilan murojaat qilinadi va maskalangan eski telefon hali amal qilishini tasdiqlash so‘raladi.

Model structured JSON qaytaradi. Server mijoz profilini yangilaydi, lead yaratadi yoki zarur bo‘lsa operatorga handoff qiladi.

## 8. Ikki tillilik

API o‘zbek lotin va rus tillaridagi ma’lumotlarni qaytaradi. Gul, nav, rang, katalog, post va notification matnlari ikkala tilda saqlanadi. AI mijoz yozgan tilni aniqlab, shu tilda davom etadi.

## 9. Xavfsizlik va audit

API JWT bilan himoyalanadi. Sklad, katalog sotuv va chiqim amallari tranzaksiya ichida bajariladi. Xodim, vaqt, eski va yangi qiymatlar audit jurnalida saqlanadi. Token va API kalitlari faqat environment variable orqali beriladi.

## 10. Birinchi ishga tushirish mezonlari

- Filiallar va rollar ishlaydi.
- Partiya dona va pochka narxida yuritiladi.
- Katalog tarkibi real sklad partiyasiga bog‘lanadi.
- Sotuvdan keyin manual chiqim notificationi yaratiladi.
- Mijoz takroran yaratilmaydi, yangi lead yaratiladi.
- Post yoki target konteksti AI’ga beriladi.
- O‘zbek va rus conversation qo‘llanadi.
- Operator handoff va qayta AI rejimi ishlaydi.
- Docker orqali backend, PostgreSQL va Redis ishga tushadi.
