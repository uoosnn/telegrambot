import os
import time
import json
import logging
from decimal import Decimal
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

TABLE_NAME = os.getenv("DYNAMODB_TABLE", "telegram_bot_state")
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")

class DynamoManager:
    def __init__(self, table_name=None):
        self.table_name = table_name or TABLE_NAME
        try:
            self.dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
            self.table = self.dynamodb.Table(self.table_name)
        except Exception as e:
            logger.warning(f"DynamoDB init warning: {e}")
            self.dynamodb = None
            self.table = None

    # Helper: Convert floats/ints for DynamoDB
    def _sanitize(self, data):
        if isinstance(data, dict):
            return {k: self._sanitize(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._sanitize(v) for v in data]
        elif isinstance(data, float):
            return Decimal(str(data))
        return data

    # 1. Sent News Management
    def get_sent_news(self) -> set:
        if not self.table:
            return set()
        try:
            response = self.table.get_item(Key={"PK": "SENT_NEWS", "SK": "URL_LIST"})
            if "Item" in response:
                return set(response["Item"].get("urls", []))
        except Exception as e:
            logger.error(f"Error fetching sent news from DynamoDB: {e}")
        return set()

    def add_sent_news(self, new_urls: list):
        if not self.table or not new_urls:
            return
        try:
            existing = self.get_sent_news()
            updated = list(existing.union(new_urls))[-1000:] # 최근 1000개 유지
            self.table.put_item(
                Item={
                    "PK": "SENT_NEWS",
                    "SK": "URL_LIST",
                    "urls": updated,
                    "updated_at": int(time.time())
                }
            )
        except Exception as e:
            logger.error(f"Error saving sent news to DynamoDB: {e}")

    # 2. Pending Messages (News -> User reply mapping)
    def save_pending_message(self, message_id: int, news_data: dict):
        if not self.table:
            return
        try:
            self.table.put_item(
                Item={
                    "PK": "PENDING_MSG",
                    "SK": str(message_id),
                    "news_data": self._sanitize(news_data),
                    "created_at": int(time.time()),
                    "ttl": int(time.time()) + (7 * 86400) # 7일 후 자동 만료
                }
            )
        except Exception as e:
            logger.error(f"Error saving pending message: {e}")

    def get_pending_message(self, message_id: int) -> dict:
        if not self.table:
            return None
        try:
            response = self.table.get_item(Key={"PK": "PENDING_MSG", "SK": str(message_id)})
            if "Item" in response:
                return response["Item"].get("news_data")
        except Exception as e:
            logger.error(f"Error getting pending message: {e}")
        return None

    def delete_pending_message(self, message_id: int):
        if not self.table:
            return
        try:
            self.table.delete_item(Key={"PK": "PENDING_MSG", "SK": str(message_id)})
        except Exception as e:
            logger.error(f"Error deleting pending message: {e}")

    # 3. Pending Confirmations (Post confirmation button callbacks)
    def save_pending_confirmation(self, message_id: int, markdown_content: str, news_info: dict):
        if not self.table:
            return
        try:
            self.table.put_item(
                Item={
                    "PK": "PENDING_CONFIRM",
                    "SK": str(message_id),
                    "markdown_content": markdown_content,
                    "news_info": self._sanitize(news_info),
                    "created_at": int(time.time()),
                    "ttl": int(time.time()) + (7 * 86400)
                }
            )
        except Exception as e:
            logger.error(f"Error saving pending confirmation: {e}")

    def get_pending_confirmation(self, message_id: int) -> dict:
        if not self.table:
            return None
        try:
            response = self.table.get_item(Key={"PK": "PENDING_CONFIRM", "SK": str(message_id)})
            if "Item" in response:
                return {
                    "markdown_content": response["Item"].get("markdown_content"),
                    "news_info": response["Item"].get("news_info")
                }
        except Exception as e:
            logger.error(f"Error getting pending confirmation: {e}")
        return None

    def delete_pending_confirmation(self, message_id: int):
        if not self.table:
            return
        try:
            self.table.delete_item(Key={"PK": "PENDING_CONFIRM", "SK": str(message_id)})
        except Exception as e:
            logger.error(f"Error deleting pending confirmation: {e}")

    # 4. API Usage Tracking
    def record_usage(self, in_tokens: int, out_tokens: int):
        if not self.table:
            return
        try:
            self.table.update_item(
                Key={"PK": "API_USAGE", "SK": "TOTAL"},
                UpdateExpression="ADD input_tokens :in_t, output_tokens :out_t",
                ExpressionAttributeValues={
                    ":in_t": in_tokens,
                    ":out_t": out_tokens
                }
            )
        except Exception as e:
            logger.error(f"Error updating API usage in DynamoDB: {e}")

    def get_usage_stats(self) -> dict:
        if not self.table:
            return {"input_tokens": 0, "output_tokens": 0}
        try:
            response = self.table.get_item(Key={"PK": "API_USAGE", "SK": "TOTAL"})
            if "Item" in response:
                item = response["Item"]
                return {
                    "input_tokens": int(item.get("input_tokens", 0)),
                    "output_tokens": int(item.get("output_tokens", 0))
                }
        except Exception as e:
            logger.error(f"Error getting API usage: {e}")
        return {"input_tokens": 0, "output_tokens": 0}

    # 5. Chat History (Per-user session in serverless)
    def save_chat_history(self, chat_id: int, history: list):
        if not self.table:
            return
        try:
            # 최근 20개 메시지만 보관 (토큰 및 용량 절약)
            trimmed = history[-20:]
            self.table.put_item(
                Item={
                    "PK": "CHAT_HISTORY",
                    "SK": str(chat_id),
                    "history": trimmed,
                    "updated_at": int(time.time()),
                    "ttl": int(time.time()) + (30 * 86400) # 30일 보관
                }
            )
        except Exception as e:
            logger.error(f"Error saving chat history: {e}")

    def get_chat_history(self, chat_id: int) -> list:
        if not self.table:
            return []
        try:
            response = self.table.get_item(Key={"PK": "CHAT_HISTORY", "SK": str(chat_id)})
            if "Item" in response:
                return response["Item"].get("history", [])
        except Exception as e:
            logger.error(f"Error fetching chat history: {e}")
        return []

    def clear_chat_history(self, chat_id: int):
        if not self.table:
            return
        try:
            self.table.delete_item(Key={"PK": "CHAT_HISTORY", "SK": str(chat_id)})
        except Exception as e:
            logger.error(f"Error clearing chat history: {e}")
