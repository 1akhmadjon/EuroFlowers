import os
import subprocess
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from django.conf import settings
import requests


def database_env():
    config = settings.DATABASES["default"]
    env = os.environ.copy()
    password = config.get("PASSWORD") or ""
    if password:
        env["PGPASSWORD"] = password
    return config, env


def pg_base_args(config):
    args = ["pg_dump"]
    host = config.get("HOST") or "localhost"
    port = str(config.get("PORT") or "5432")
    user = config.get("USER") or ""
    db_name = config.get("NAME") or ""
    args.extend(["-h", host, "-p", port])
    if user:
        args.extend(["-U", user])
    args.append(db_name)
    return args


def create_database_backups(temp_dir):
    config, env = database_env()
    engine = config.get("ENGINE", "")
    if "postgresql" not in engine:
        raise RuntimeError("Backup faqat PostgreSQL uchun sozlangan")
    dump_path = Path(temp_dir) / "euroflowers_database.dump"
    sql_path = Path(temp_dir) / "euroflowers_database.sql"
    subprocess.run(pg_base_args(config)[:-1] + ["-Fc", "-f", str(dump_path), pg_base_args(config)[-1]], check=True, env=env, capture_output=True, text=True)
    subprocess.run(pg_base_args(config)[:-1] + ["--clean", "--if-exists", "-f", str(sql_path), pg_base_args(config)[-1]], check=True, env=env, capture_output=True, text=True)
    return [dump_path, sql_path]


def create_media_backup(temp_dir):
    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.exists():
        return None
    files = [path for path in media_root.rglob("*") if path.is_file()]
    if not files:
        return None
    zip_path = Path(temp_dir) / "euroflowers_media.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(media_root))
    return zip_path


def send_telegram_document(path, caption):
    token = settings.BACKUP_BOT_TOKEN
    chat_id = settings.BACKUP_TELEGRAM_GROUP_ID
    if not token or not chat_id:
        raise RuntimeError("Backup bot token yoki group id sozlanmagan")
    data = {"chat_id": chat_id, "caption": caption}
    if settings.BACKUP_TELEGRAM_THREAD_ID:
        data["message_thread_id"] = settings.BACKUP_TELEGRAM_THREAD_ID
    with open(path, "rb") as file:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data=data,
            files={"document": (path.name, file)},
            timeout=120,
        )
    response.raise_for_status()
    return response.json()


def send_backup_to_telegram(triggered_by="auto"):
    with TemporaryDirectory(prefix="euroflowers-backup-") as temp_dir:
        paths = create_database_backups(temp_dir)
        media = create_media_backup(temp_dir)
        if media:
            paths.append(media)
        results = []
        for path in paths:
            caption = f"EuroFlowers backup\nTrigger: {triggered_by}\nFile: {path.name}"
            results.append(send_telegram_document(path, caption))
        return {"sent": len(results), "files": [path.name for path in paths], "triggered_by": triggered_by}


def backup_command_matches(payload):
    message = payload.get("message") or {}
    text = (message.get("text") or "").strip()
    if not text:
        return False
    command = settings.BACKUP_TELEGRAM_COMMAND or "/eurodan_backup_tashachi"
    first = text.split()[0].split("@")[0]
    if first != command:
        return False
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if settings.BACKUP_TELEGRAM_GROUP_ID and chat_id != str(settings.BACKUP_TELEGRAM_GROUP_ID):
        return False
    thread_id = str(message.get("message_thread_id") or "")
    if settings.BACKUP_TELEGRAM_THREAD_ID and thread_id != str(settings.BACKUP_TELEGRAM_THREAD_ID):
        return False
    return True
