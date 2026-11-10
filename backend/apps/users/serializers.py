import logging
from typing import Any

from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers

logger = logging.getLogger(__name__)

# One message for a wrong password, an unknown user and an inactive user, so that the
# response does not reveal which accounts exist.
INVALID_CREDENTIALS = "Неверный логин или пароль."


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    # Not trimmed (spaces may be part of a password); the length cap bounds hashing work.
    password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None:
            # Neither the username nor the password is logged.
            logger.warning("Failed login attempt")
            raise serializers.ValidationError(INVALID_CREDENTIALS, code="invalid_credentials")
        attrs["user"] = user
        return attrs


class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["id", "username", "first_name", "last_name", "role"]
        read_only_fields = fields
