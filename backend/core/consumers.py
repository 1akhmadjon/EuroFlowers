from urllib.parse import parse_qs
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import AnonymousUser, User
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = await self.get_user()
        if not self.user or not self.user.is_authenticated:
            await self.close(code=4401)
            return
        self.group_name = f"notifications_user_{self.user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notification(self, event):
        await self.send_json(event["payload"])

    @database_sync_to_async
    def get_user(self):
        params = parse_qs((self.scope.get("query_string") or b"").decode())
        token = (params.get("token") or [""])[0]
        if not token:
            return AnonymousUser()
        try:
            validated = JWTAuthentication().get_validated_token(token)
            return JWTAuthentication().get_user(validated)
        except (InvalidToken, TokenError):
            return AnonymousUser()
