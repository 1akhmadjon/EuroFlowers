import os
import sys
from pathlib import Path
import dj_database_url
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("SECRET_KEY", "local-development-key-for-euroflowers-development-only")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0,192.168.1.5,.ngrok-free.dev,.ngrok-free.app,.ngrok.io").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "channels",
    "rest_framework",
    "drf_spectacular",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "core.apps.CoreConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [os.getenv("REDIS_URL", "redis://localhost:6379/0")]},
    }
}

DATABASES = {"default": dj_database_url.config(default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "uz-latn"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework_simplejwt.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend", "rest_framework.filters.SearchFilter", "rest_framework.filters.OrderingFilter"),
    # vaqtlar hamma javobda mahalliy vaqtda (+05:00) qaytadi
    "DEFAULT_RENDERER_CLASSES": ("core.renderers.LocalTimeJSONRenderer", "rest_framework.renderers.BrowsableAPIRenderer"),
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardPagination",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "PAGE_SIZE": 30,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "EuroFlowers API",
    "DESCRIPTION": "EuroFlowers backend API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173,http://192.168.1.5:3000,http://192.168.1.5:5173").split(",")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", os.getenv("OPENAI_MODEL", "gpt-5-mini"))
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "low")
# Gul navini (Alfalob, Jumila, London, Katalina) ajratish uchun past sifatli rasm
# yetmaydi — 512px da gulbarg shakli yo'qoladi. Shuning uchun bu yerda default high.
OPENAI_VISION_DETAIL = os.getenv("OPENAI_VISION_DETAIL", "high")
OPENAI_VISION_REASONING = os.getenv("OPENAI_VISION_REASONING", "low")
OPENAI_VISION_CROWDED_REASONING = os.getenv("OPENAI_VISION_CROWDED_REASONING", "medium")
AI_CATALOG_MATCH_SHORTLIST = int(os.getenv("AI_CATALOG_MATCH_SHORTLIST", "4"))
AI_CATALOG_MATCH_MIN_SCORE = int(os.getenv("AI_CATALOG_MATCH_MIN_SCORE", "55"))
AI_TEST_INSTAGRAM_USERNAMES = [value.strip().lower().lstrip("@") for value in os.getenv("AI_TEST_INSTAGRAM_USERNAMES", "extra_teest").split(",") if value.strip()]
AI_TEST_INSTAGRAM_USER_IDS = [value.strip() for value in os.getenv("AI_TEST_INSTAGRAM_USER_IDS", "").split(",") if value.strip()]
# Test uchun ajratilgan Instagram akkaunt. Shu akkauntga kelgan har qanday xabar
# test hisoblanadi: kim yozganidan qat'i nazar AI javob beradi.
AI_TEST_INSTAGRAM_ACCOUNT_IDS = [value.strip() for value in os.getenv("AI_TEST_INSTAGRAM_ACCOUNT_IDS", "17841476392326035").split(",") if value.strip()]
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
INSTAGRAM_VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN", "change-me")
INSTAGRAM_API_VERSION = os.getenv("INSTAGRAM_API_VERSION", "v23.0")
INSTAGRAM_ACCOUNT_ACCESS_TOKENS = [value.strip() for value in os.getenv("INSTAGRAM_ACCOUNT_ACCESS_TOKENS", "").split(",") if value.strip()]
TELEGRAM_GROUP_CHAT_ID = os.getenv("TELEGRAM_GROUP_CHAT_ID", "")
# Test paytida hech qanday tashqi xabar ketmasligi kerak. Sotuv guruhiga
# yuboriladigan xabar tokenni .env dan oladi, shuning uchun test yugurtirilganda
# haqiqiy Telegram guruhiga test sotuvlari tushib qolardi.
TESTING = "test" in sys.argv

SALE_TELEGRAM_BOT_TOKEN = os.getenv("SALE_TELEGRAM_BOT_TOKEN", "")
SALE_TELEGRAM_GROUP_CHAT_ID = os.getenv("SALE_TELEGRAM_GROUP_CHAT_ID", "")
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TIMEZONE = TIME_ZONE
BACKUP_BOT_TOKEN = os.getenv("BACKUP_BOT_TOKEN", "")
BACKUP_TELEGRAM_GROUP_ID = os.getenv("BACKUP_TELEGRAM_GROUP_ID", "")
BACKUP_TELEGRAM_THREAD_ID = os.getenv("BACKUP_TELEGRAM_THREAD_ID", "")
BACKUP_TELEGRAM_COMMAND = os.getenv("BACKUP_TELEGRAM_COMMAND", "/eurodan_backup_tashachi")
AI_OPERATOR_HANDOFF_BOT_TOKEN = os.getenv("AI_OPERATOR_HANDOFF_BOT_TOKEN", "8752325160:AAH4G3mm0ZlPgXI3nRqKhz9nvF5KuOYdixA")
AI_OPERATOR_HANDOFF_GROUP_ID = os.getenv("AI_OPERATOR_HANDOFF_GROUP_ID", "-5195454751")
AI_OPERATOR_HANDOFF_THREAD_ID = os.getenv("AI_OPERATOR_HANDOFF_THREAD_ID", "")
# Eslatma guruhi. Oddiy guruh superguruhga o'tsa id o'zgaradi — yangi id
# IntegrationSettings.extra ga eslab qolinadi va shu qiymatdan ustun turadi.
AI_RECALL_GROUP_ID = os.getenv("AI_RECALL_GROUP_ID", "-5385608916")
# Mijoz manzilni xaritada belgilaydigan sahifa. {lead_id} va {token} o'rniga
# leadning raqami va maxfiy kodi qo'yiladi. Sozlanmasa AI havola bermaydi.
DELIVERY_LOCATION_URL = os.getenv("DELIVERY_LOCATION_URL", "")
FRONTEND_CHAT_URL = os.getenv("FRONTEND_CHAT_URL", "https://euroflowers.cognilabs.org/chat?conversation_id={conversation_id}")
CELERY_BEAT_SCHEDULE = {
    "lead-recalls-every-minute": {
        "task": "core.tasks.process_due_lead_recalls",
        "schedule": 60.0,
    },
    "daily-telegram-backup": {
        "task": "core.tasks.send_telegram_backup",
        "schedule": crontab(hour=3, minute=0),
    }
}
