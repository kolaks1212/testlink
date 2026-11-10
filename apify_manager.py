import asyncio
import logging
from apify_client import ApifyClientAsync
from config import APIFY_TOKEN, ACTOR_ID
import aiohttp
import aiofiles
import os

logger = logging.getLogger(__name__)


class ApifyManager:
    def __init__(self):
        self.client = ApifyClientAsync(APIFY_TOKEN)

    async def start_download(
        self,
        urls: list[str],
        create_zip: bool = True,
        max_files: int = 500,
        images_only: bool = False,
    ):
        """Start the Bulk File Downloader Actor"""
        run_input = {
            "urls": urls,
            "createZip": create_zip,
            "maxFiles": max_files,
            "imagesOnly": images_only,
            "retryFailed": True,
        }

        run = await self.client.actor(ACTOR_ID).start(run_input=run_input)
        # In newer apify-client versions, start() returns a Run object (not dict)
        run_id = getattr(run, "id", None) or (run.get("id") if isinstance(run, dict) else None)
        logger.info(f"Started Actor run: {run_id}")
        return run

    async def wait_for_run(self, run_id: str, timeout_secs: int = 1800):
        """Wait until the run finishes"""
        from datetime import timedelta
        # Newer apify-client uses wait_duration (timedelta), older used wait_secs
        try:
            run = await self.client.run(run_id).wait_for_finish(
                wait_duration=timedelta(seconds=timeout_secs)
            )
        except TypeError:
            # Fallback for older client versions
            run = await self.client.run(run_id).wait_for_finish(wait_secs=timeout_secs)
        return run

    async def get_run_info(self, run_id: str):
        """Get current status of a run"""
        run = await self.client.run(run_id).get()
        return run

    async def download_zip_from_store(self, store_id: str, local_path: str) -> str | None:
        """
        Try to download the ZIP file from Key-Value Store.
        Returns the local file path if successful, otherwise None.
        """
        try:
            # Common keys used by this Actor
            possible_keys = ["OUTPUT.zip", "output.zip", "download.zip", "files.zip"]

            for key in possible_keys:
                try:
                    record = await self.client.key_value_store(store_id).get_record(key)
                    if record is None:
                        continue
                    # Support both dict and object
                    content = getattr(record, "value", None) or (record.get("value") if isinstance(record, dict) else None)
                    if content and isinstance(content, (bytes, bytearray)):
                        async with aiofiles.open(local_path, "wb") as f:
                            await f.write(content)
                        logger.info(f"Downloaded ZIP from key: {key}")
                        return local_path
                except Exception:
                    continue

            # Fallback: list all keys and look for .zip
            keys_result = await self.client.key_value_store(store_id).list_keys()
            items = getattr(keys_result, "items", None) or (keys_result.get("items") if isinstance(keys_result, dict) else []) or []
            for item in items:
                key = getattr(item, "key", None) or (item.get("key") if isinstance(item, dict) else "") or ""
                if key.lower().endswith(".zip"):
                    record = await self.client.key_value_store(store_id).get_record(key)
                    if record is None:
                        continue
                    # Support both dict and object
                    content = getattr(record, "value", None) or (record.get("value") if isinstance(record, dict) else None)
                    if content and isinstance(content, (bytes, bytearray)):
                        async with aiofiles.open(local_path, "wb") as f:
                            await f.write(content)
                        logger.info(f"Downloaded ZIP from key: {key}")
                        return local_path

            logger.warning("No ZIP file found in Key-Value Store")
            return None

        except Exception as e:
            logger.error(f"Error downloading ZIP: {e}")
            return None

    async def get_dataset_summary(self, dataset_id: str) -> dict:
        """Get a summary of downloaded files from the dataset"""
        items = []
        async for item in self.client.dataset(dataset_id).iterate_items():
            items.append(item)
    
        def is_success(i):
            if not isinstance(i, dict):
                i = dict(i) if hasattr(i, "__dict__") else {}
            status = str(i.get("status") or i.get("state") or "").lower()
            if status in ("success", "succeeded", "ok", "downloaded"):
                return True
            if i.get("success") is True:
                return True
            if i.get("error") or i.get("errorMessage") or i.get("failReason"):
                return False
            # If has a file size / path and no error → success
            if (i.get("size") or i.get("fileSize") or i.get("path") or i.get("filename")) and not i.get("error"):
                return True
            return False
    
        success = [i for i in items if is_success(i)]
        failed = [i for i in items if i not in success]
    
        # Extract short failure reasons for user
        reasons = []
        for i in failed[:5]:
            if not isinstance(i, dict):
                continue
            reason = (
                i.get("error")
                or i.get("errorMessage")
                or i.get("failReason")
                or i.get("message")
                or i.get("status")
                or "unknown"
            )
            url = i.get("url") or i.get("sourceUrl") or i.get("input") or ""
            reasons.append(f"• {url[:60]} → {reason}")
    
        return {
            "total": len(items),
            "success": len(success),
            "failed": len(failed),
            "items": items[:20],
            "reasons": reasons,
        }
