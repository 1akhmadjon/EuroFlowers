"""AI katalog rasmlarining tahlilini (fingerprint) yasab qo'yish.

Yangi mahsulot qo'shilganda buni signal o'zi qiladi. Bu buyruq eski mahsulotlar uchun
bir marta yuritiladi yoki tahlil qoidasi o'zgargach hammasini qayta yasash uchun kerak.
"""

from django.core.management.base import BaseCommand

from core.models import AICatalogItem
from core.vision_services import ensure_catalog_fingerprint, fingerprint_is_stale


class Command(BaseCommand):
    help = "AI katalog mahsulotlari uchun rasm tahlilini yasaydi"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Fingerprinti bor mahsulotlarni ham qayta tahlil qiladi")
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        queryset = AICatalogItem.objects.filter(is_active=True).exclude(image_url="").order_by("id")[:options["limit"]]
        built = 0
        skipped = 0
        failed = 0
        for item in queryset:
            if not options["force"] and not fingerprint_is_stale(item):
                skipped += 1
                continue
            fingerprint = ensure_catalog_fingerprint(item, force=options["force"])
            if fingerprint:
                built += 1
                self.stdout.write(f"{item.id} {item.name} -> {fingerprint.get('flower_form')} {fingerprint.get('dominant_colors')} {fingerprint.get('container')}")
            else:
                failed += 1
                self.stdout.write(self.style.WARNING(f"{item.id} {item.name} -> tahlil qilinmadi"))
        self.stdout.write(self.style.SUCCESS(f"yasaldi={built} o'tkazildi={skipped} xato={failed}"))
