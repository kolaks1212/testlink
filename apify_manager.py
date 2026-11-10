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
        logger.info(f"Started Actor run: {run['id']}")
        return run

    async def wait_for_run(self, run_id: str, timeout_secs: int = 1800):
        """Wait until the run finishes"""
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
                    if record and record.get("value"):
                        content = record["value"]
                        # If it's bytes
                        if isinstance(content, bytes):
                            async with aiofiles.open(local_path, "wb") as f:
                                await f.write(content)
                            logger.info(f"Downloaded ZIP from key: {key}")
                            return local_path
                except Exception:
                    continue

            # Fallback: list all keys and look for .zip
            keys = await self.client.key_value_store(store_id).list_keys()
            for item in keys.get("items", []):
                key = item.get("key", "")
                if key.lower().endswith(".zip"):
                    record = await self.client.key_value_store(store_id).get_record(key)
                    if record and record.get("value"):
                        content = record["value"]
                        if isinstance(content, bytes):
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

        success = [i for i in items if i.get("status") == "success" or i.get("success") is True]
        failed = [i for i in items if i not in success]

        return {
            "total": len(items),
            "success": len(success),
            "failed": len(failed),
            "items": items[:20],  # only first 20 for preview
        }
