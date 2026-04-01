"""Provider manager for multi-Bedrock-account support."""
import json
from datetime import datetime, timezone
from typing import Dict
from uuid import uuid4

from botocore.exceptions import ClientError

from app.keypool.encryption import KeyEncryption


class ProviderManager:
    """CRUD manager for Bedrock provider configurations with encrypted credentials."""

    def __init__(
        self,
        dynamodb_resource,
        table_name: str = "anthropic-proxy-providers",
        encryption_secret: str = "",
    ):
        self.table = dynamodb_resource.Table(table_name)
        self.table_name = table_name
        self._encryption = KeyEncryption(encryption_secret) if encryption_secret else None

    def _encrypt_credentials(self, credentials: Dict[str, str]) -> str:
        plaintext = json.dumps(credentials)
        if self._encryption:
            return self._encryption.encrypt(plaintext)
        return plaintext

    def _decrypt_credentials(self, encrypted: str) -> Dict[str, str]:
        if self._encryption:
            plaintext = self._encryption.decrypt(encrypted)
        else:
            plaintext = encrypted
        return json.loads(plaintext)

    def _mask_credentials(self, auth_type: str, credentials: Dict[str, str]) -> str:
        if auth_type == "bearer_token":
            token = credentials.get("bearer_token", "")
            return KeyEncryption.mask(token)
        elif auth_type == "ak_sk":
            ak = credentials.get("access_key_id", "")
            sk = credentials.get("secret_access_key", "")
            return f"AK: {ak}, SK: {KeyEncryption.mask(sk)}"
        return "****"

    def create_provider(self, name, aws_region, auth_type, credentials, endpoint_url=None):
        now = datetime.now(timezone.utc).isoformat()
        provider_id = uuid4().hex
        encrypted = self._encrypt_credentials(credentials)
        masked = self._mask_credentials(auth_type, credentials)
        item = {
            "provider_id": provider_id,
            "name": name,
            "aws_region": aws_region,
            "auth_type": auth_type,
            "encrypted_credentials": encrypted,
            "masked_credentials": masked,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        if endpoint_url:
            item["endpoint_url"] = endpoint_url
        self.table.put_item(Item=item)
        return item

    def get_provider(self, provider_id):
        try:
            response = self.table.get_item(Key={"provider_id": provider_id})
            return response.get("Item")
        except ClientError:
            return None

    def list_providers(self):
        response = self.table.scan()
        return response.get("Items", [])

    def get_decrypted_credentials(self, provider_id):
        provider = self.get_provider(provider_id)
        if not provider:
            return None
        return self._decrypt_credentials(provider["encrypted_credentials"])

    def update_provider(self, provider_id, **kwargs):
        provider = self.get_provider(provider_id)
        if not provider:
            return False

        update_expr_parts = []
        attr_names = {}
        attr_values = {}

        credentials = kwargs.pop("credentials", None)
        if credentials:
            encrypted = self._encrypt_credentials(credentials)
            auth_type = kwargs.get("auth_type", provider.get("auth_type", "bearer_token"))
            masked = self._mask_credentials(auth_type, credentials)
            update_expr_parts.append("#ec = :ec")
            attr_names["#ec"] = "encrypted_credentials"
            attr_values[":ec"] = encrypted
            update_expr_parts.append("#mc = :mc")
            attr_names["#mc"] = "masked_credentials"
            attr_values[":mc"] = masked

        for key, value in kwargs.items():
            if value is not None:
                placeholder = f"#{key}"
                val_placeholder = f":{key}"
                update_expr_parts.append(f"{placeholder} = {val_placeholder}")
                attr_names[placeholder] = key
                attr_values[val_placeholder] = value

        if not update_expr_parts:
            return True

        update_expr_parts.append("#ua = :ua")
        attr_names["#ua"] = "updated_at"
        attr_values[":ua"] = datetime.now(timezone.utc).isoformat()

        try:
            self.table.update_item(
                Key={"provider_id": provider_id},
                UpdateExpression="SET " + ", ".join(update_expr_parts),
                ExpressionAttributeNames=attr_names,
                ExpressionAttributeValues=attr_values,
            )
            return True
        except ClientError:
            return False

    def delete_provider(self, provider_id):
        provider = self.get_provider(provider_id)
        if not provider:
            return False
        try:
            self.table.delete_item(Key={"provider_id": provider_id})
            return True
        except ClientError:
            return False
