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
        self._local_targets = {}
        self._processed_updates = set()
        self._local_locks = {}
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

    # 6. Post Delete/Unpublish Target Mapping (Inline Keyboard Callback Token)
    def save_delete_target(self, token_id: str, folder: str, filename: str):
        target_data = {"folder": folder, "filename": filename}
        self._local_targets[token_id] = target_data
        if not self.table:
            return
        try:
            self.table.put_item(
                Item={
                    "PK": "DEL_TARGET",
                    "SK": str(token_id),
                    "folder": folder,
                    "filename": filename,
                    "created_at": int(time.time()),
                    "ttl": int(time.time()) + (2 * 86400) # 2일 후 자동 만료
                }
            )
        except Exception as e:
            logger.error(f"Error saving delete target: {e}")

    def get_delete_target(self, token_id: str) -> dict:
        if token_id in self._local_targets:
            return self._local_targets[token_id]
        if not self.table:
            return None
        try:
            response = self.table.get_item(Key={"PK": "DEL_TARGET", "SK": str(token_id)})
            if "Item" in response:
                return {
                    "folder": response["Item"].get("folder"),
                    "filename": response["Item"].get("filename")
                }
        except Exception as e:
            logger.error(f"Error getting delete target: {e}")
        return None

    def delete_delete_target(self, token_id: str):
        self._local_targets.pop(token_id, None)
        if not self.table:
            return
        try:
            self.table.delete_item(Key={"PK": "DEL_TARGET", "SK": str(token_id)})
        except Exception as e:
            logger.error(f"Error deleting delete target: {e}")

    def record_recent_post(self, folder: str, filename: str):
        if not self.table:
            return
        try:
            existing = self.get_recent_posts()
            entry = {"folder": folder, "filename": filename}
            filtered = [p for p in existing if not (p.get("folder") == folder and p.get("filename") == filename)]
            updated = ([entry] + filtered)[:30]
            self.table.put_item(
                Item={
                    "PK": "RECENT_POSTS",
                    "SK": "LIST",
                    "posts": updated,
                    "updated_at": int(time.time())
                }
            )
        except Exception as e:
            logger.error(f"Error recording recent post: {e}")

    def get_recent_posts(self) -> list:
        if not self.table:
            return []
        try:
            response = self.table.get_item(Key={"PK": "RECENT_POSTS", "SK": "LIST"})
            if "Item" in response:
                return response["Item"].get("posts", [])
        except Exception as e:
            logger.error(f"Error getting recent posts: {e}")
        return []

    def remove_recent_post(self, folder: str, filename: str):
        if not self.table:
            return
        try:
            existing = self.get_recent_posts()
            updated = [p for p in existing if not (p.get("folder") == folder and p.get("filename") == filename)]
            if len(updated) != len(existing):
                self.table.put_item(
                    Item={
                        "PK": "RECENT_POSTS",
                        "SK": "LIST",
                        "posts": updated,
                        "updated_at": int(time.time())
                    }
                )
        except Exception as e:
            logger.error(f"Error removing recent post: {e}")

    # 7. Webhook Deduplication (Idempotency) & Per-Chat Post Mutex Lock
    def claim_update_id(self, update_id: int) -> bool:
        """
        텔레그램 웹훅의 update_id를 원자적 조건부 쓰기(attribute_not_exists)로 선점합니다.
        이미 처리 중이거나 처리된 update_id(타임아웃 재시도 요청)인 경우 False를 반환합니다.
        """
        if update_id is None:
            return True
        uid_str = str(update_id)
        if uid_str in self._processed_updates:
            return False

        now = int(time.time())
        if not self.table:
            self._processed_updates.add(uid_str)
            return True

        try:
            self.table.put_item(
                Item={
                    "PK": "PROCESSED_UPDATE",
                    "SK": uid_str,
                    "created_at": now,
                    "ttl": now + 86400  # 24시간 후 자동 삭제
                },
                ConditionExpression="attribute_not_exists(PK)"
            )
            self._processed_updates.add(uid_str)
            return True
        except ClientError as ce:
            if ce.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                logger.warning(f"[Idempotency] Duplicate Telegram update_id={uid_str} blocked.")
                self._processed_updates.add(uid_str)
                return False
            logger.error(f"DynamoDB error in claim_update_id: {ce}")
            return True
        except Exception as e:
            logger.error(f"Unexpected error in claim_update_id: {e}")
            return True

    def acquire_post_lock(self, chat_id: int, ttl_sec: int = 120) -> bool:
        """
        동일 채팅방에서 블로그 생성/번역 작업이 중복 실행되지 않도록 분산 락을 획득합니다.
        이전 락이 없거나 만료(ttl < now)된 경우에만 True를 반환합니다.
        """
        cid_str = str(chat_id)
        now = int(time.time())

        if not self.table:
            exp = self._local_locks.get(cid_str, 0)
            if exp > now:
                return False
            self._local_locks[cid_str] = now + ttl_sec
            return True

        try:
            self.table.put_item(
                Item={
                    "PK": "POST_LOCK",
                    "SK": cid_str,
                    "locked_at": now,
                    "ttl": now + ttl_sec
                },
                ConditionExpression="attribute_not_exists(PK) OR #ttl < :now",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={":now": now}
            )
            return True
        except ClientError as ce:
            if ce.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                logger.warning(f"[PostLock] Concurrent post job blocked for chat_id={cid_str}")
                return False
            logger.error(f"DynamoDB error in acquire_post_lock: {ce}")
            return True
        except Exception as e:
            logger.error(f"Unexpected error in acquire_post_lock: {e}")
            return True

    def release_post_lock(self, chat_id: int):
        """블로그 생성/번역 완료 또는 실패 시 즉시 분산 락을 해제합니다."""
        cid_str = str(chat_id)
        self._local_locks.pop(cid_str, None)
        if not self.table:
            return
        try:
            self.table.delete_item(Key={"PK": "POST_LOCK", "SK": cid_str})
        except Exception as e:
            logger.error(f"Error releasing post lock: {e}")
