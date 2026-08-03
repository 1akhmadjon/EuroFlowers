"""Vaqtlarni mahalliy vaqtda qaytaradigan JSON chiqaruvchi.

Django ma'lumotni UTC da saqlaydi. DRF serializer maydonlari uni mahalliy
vaqtga o'giradi, lekin hisobot endpointlari xom lug'at qaytaradi va u UTC
bo'lib ketardi. Natijada bitta sotuv bir joyda 22:09, boshqa joyda 17:09
bo'lib ko'rinardi. Bu chiqaruvchi hammasini bir xil qiladi.
"""

import datetime

from django.utils import timezone
from rest_framework.renderers import JSONRenderer
from rest_framework.utils.encoders import JSONEncoder


def to_local(value):
    if isinstance(value, datetime.datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


class LocalTimeJSONEncoder(JSONEncoder):
    def default(self, obj):
        return super().default(to_local(obj))


class LocalTimeJSONRenderer(JSONRenderer):
    encoder_class = LocalTimeJSONEncoder
