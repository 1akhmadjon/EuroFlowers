"""Ro'yxat endpointlari uchun bir xil sahifalash va umumiy sonlar.

Ilgari javobda faqat `count`, `next`, `previous`, `results` bor edi. Frontend
"1-30 / 412" deb yozish uchun `next` havolasini o'zi tahlil qilishi kerak edi,
jamini ko'rsatish uchun esa hamma sahifani aylanib chiqishi kerak edi.

Endi har bir sahifalangan javobda sahifa raqami, sahifalar soni va — buni
qo'llagan ro'yxatlarda — butun filtr bo'yicha hisoblangan `totals` bo'ladi.
"""

from collections import OrderedDict
from decimal import Decimal

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

# page_size shu qiymatlardan biri bo'lsa hamma yozuv bitta sahifada qaytadi
ALL_PAGE_VALUES = {"all", "hammasi", "0", "-1"}


def money(value):
    """Pul qiymatini serializerlar bilan bir xil ko'rinishda qaytaradi."""
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def row_count(queryset):
    """QuerySet uchun ham, oddiy ro'yxat uchun ham element soni."""
    try:
        return queryset.count()
    except TypeError:  # list.count() argumentsiz ishlamaydi
        return len(queryset)


class StandardPagination(PageNumberPagination):
    """Barcha ro'yxatlar uchun bitta sahifalash formati.

    `page_size=all` (yoki 0) berilsa hamma yozuv bitta sahifada qaytadi, lekin
    javob ko'rinishi o'zgarmaydi — natija baribir `results` ichida, `count` esa
    joyida qoladi. Bu ochiluvchi ro'yxatlar (dropdown) uchun kerak: ilgari
    ular jimgina 30 tada kesilib qolardi.
    """

    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 200

    def paginate_queryset(self, queryset, request, view=None):
        self._all_requested = self._wants_everything(request)
        if self._all_requested:
            self._all_count = row_count(queryset)
        return super().paginate_queryset(queryset, request, view)

    def _wants_everything(self, request):
        raw = request.query_params.get(self.page_size_query_param)
        return str(raw or "").strip().lower() in ALL_PAGE_VALUES

    def get_page_size(self, request):
        if getattr(self, "_all_requested", False):
            # bo'sh ro'yxatda ham paginator ishlashi uchun kamida 1
            return max(int(getattr(self, "_all_count", 0)), 1)
        return super().get_page_size(request)

    def build_payload(self, data):
        page = self.page
        paginator = page.paginator
        return OrderedDict([
            ("count", paginator.count),
            ("page", page.number),
            ("page_size", paginator.per_page),
            ("total_pages", paginator.num_pages),
            ("has_next", page.has_next()),
            ("has_previous", page.has_previous()),
            ("next", self.get_next_link()),
            ("previous", self.get_previous_link()),
            ("results", data),
        ])

    def get_paginated_response(self, data):
        return Response(self.build_payload(data))

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "required": ["count", "page", "page_size", "total_pages", "results"],
            "properties": {
                "count": {"type": "integer", "example": 412, "description": "Filtr bo‘yicha jami yozuv"},
                "page": {"type": "integer", "example": 1},
                "page_size": {"type": "integer", "example": 30},
                "total_pages": {"type": "integer", "example": 14},
                "has_next": {"type": "boolean", "example": True},
                "has_previous": {"type": "boolean", "example": False},
                "next": {"type": "string", "nullable": True, "format": "uri"},
                "previous": {"type": "string", "nullable": True, "format": "uri"},
                "results": schema,
                "totals": {
                    "type": "object",
                    "nullable": True,
                    "additionalProperties": True,
                    "description": "Butun filtr bo‘yicha umumiy sonlar (faqat shuni qo‘llagan ro‘yxatlarda)",
                },
            },
        }


class TotalsListMixin:
    """Sahifalangan ro'yxatga butun filtr bo'yicha umumiy sonlarni qo'shadi.

    Sahifada 30 ta yozuv ko'rinsa ham `totals` ichidagi sonlar filtrga tushgan
    hamma yozuv bo'yicha hisoblanadi. Frontend jamini o'zi yig'masligi kerak.

    Viewset faqat `get_list_totals(queryset)` ni yozadi — qolgani shu yerda.
    """

    def get_list_totals(self, queryset):
        return {}

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        totals = self.get_list_totals(queryset)
        page = self.paginate_queryset(queryset)
        if page is not None:
            response = self.get_paginated_response(self.get_serializer(page, many=True).data)
            response.data["totals"] = totals
            return response
        data = self.get_serializer(queryset, many=True).data
        return Response(OrderedDict([
            ("count", len(data)),
            ("page", 1),
            ("page_size", len(data)),
            ("total_pages", 1),
            ("has_next", False),
            ("has_previous", False),
            ("next", None),
            ("previous", None),
            ("results", data),
            ("totals", totals),
        ]))
