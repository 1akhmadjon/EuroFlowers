"""Do'kondagi qurilma uchun yuzdan tanish.

Yopiq va kichik to'plam uchun (10-20 florist, bir xil yorug'lik) OpenCV LBPH yetarli
va tez ishlaydi. Og'ir model va GPU kerak emas.

Kutubxona o'rnatilmagan bo'lsa modul yiqilmaydi, faqat FACE_AVAILABLE False bo'ladi
va endpoint tushunarli xato qaytaradi.
"""

import base64

from django.core.files.storage import default_storage

try:
    import cv2
    import numpy as np

    FACE_AVAILABLE = hasattr(cv2, "face")
except Exception:  # pragma: no cover - kutubxona yo'q muhitda
    cv2 = None
    np = None
    FACE_AVAILABLE = False


FACE_SIZE = (200, 200)
# LBPH da kichik masofa ko'proq o'xshashlik. Do'kon sharoitida 70 atrofi ishonchli chegara.
MATCH_THRESHOLD = 70.0
_cache = {"key": None, "recognizer": None, "labels": {}}


class FaceError(Exception):
    pass


def _require_cv2():
    if not FACE_AVAILABLE:
        raise FaceError("Yuzni tanish kutubxonasi o‘rnatilmagan. opencv-contrib-python-headless kerak.")


def decode_image(data):
    """base64 matn, bayt yoki yuklangan fayldan rasm o'qiydi."""
    _require_cv2()
    if hasattr(data, "read"):
        raw = data.read()
    elif isinstance(data, str):
        payload = data.split(",", 1)[1] if data.startswith("data:") else data
        try:
            raw = base64.b64decode(payload)
        except Exception as error:
            raise FaceError("Rasmni o‘qib bo‘lmadi") from error
    else:
        raw = data
    array = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FaceError("Rasm formati tushunarsiz")
    return image


def detect_face(image):
    """Rasmdan bitta yuzni kesib oladi va standart o'lchamga keltiradi."""
    _require_cv2()
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    faces = cascade.detectMultiScale(image, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
    if len(faces) == 0:
        raise FaceError("Rasmda yuz topilmadi. Kameraga to‘g‘ri qarab qayta urinib ko‘ring.")
    x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
    return cv2.resize(image[y : y + h, x : x + w], FACE_SIZE)


def face_from_source(source):
    return detect_face(decode_image(source))


def encode_face(face):
    """Kesilgan yuzni bazada saqlash uchun ro'yxatga aylantiradi."""
    return face.flatten().astype(np.uint8).tolist()


def decode_descriptor(descriptor):
    array = np.array(descriptor, dtype=np.uint8)
    return array.reshape(FACE_SIZE)


def _samples_key(samples):
    return tuple(sorted((row.id, row.updated_at.timestamp()) for row in samples))


def build_recognizer(samples):
    """Faol namunalardan tanuvchi yig'adi. Namunalar o'zgarmasa keshdan oladi."""
    _require_cv2()
    key = _samples_key(samples)
    if _cache["key"] == key and _cache["recognizer"] is not None:
        return _cache["recognizer"], _cache["labels"]
    images = []
    labels = []
    label_map = {}
    for row in samples:
        if not row.descriptor:
            continue
        label = label_map.setdefault(row.florist_id, len(label_map))
        images.append(decode_descriptor(row.descriptor))
        labels.append(label)
    if not images:
        raise FaceError("Hali birorta yuz namunasi ro‘yxatdan o‘tkazilmagan.")
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(images, np.array(labels))
    reverse = {value: key_id for key_id, value in label_map.items()}
    _cache.update({"key": key, "recognizer": recognizer, "labels": reverse})
    return recognizer, reverse


def recognize(source, samples):
    """Rasmdan floristni aniqlaydi. (florist_id, ishonch) qaytaradi."""
    face = face_from_source(source)
    recognizer, reverse = build_recognizer(samples)
    label, distance = recognizer.predict(face)
    if distance > MATCH_THRESHOLD:
        raise FaceError("Yuz tanilmadi. Qayta urinib ko‘ring yoki administratorga murojaat qiling.")
    confidence = round(max(0.0, 100.0 - distance), 2)
    return reverse.get(label), confidence


def invalidate_cache():
    _cache.update({"key": None, "recognizer": None, "labels": {}})


def save_face_image(uploaded, florist_id):
    path = default_storage.save(f"faces/florist_{florist_id}_{uploaded.name}", uploaded)
    return path, default_storage.url(path)
