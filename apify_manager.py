import logging
from datetime import timedelta

import aiofiles
from apify_client import ApifyClientAsync

from config import (
    APIFY_TOKEN,
    ACTOR_ID,
    USE_RESIDENTIAL_PROXY,
    PROXY_COUNTRY,
)

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
        """Start dz_omar/universal-downloader with optional Residential proxy."""
        url_items = [{"url": u, "method": "GET"} for u in urls[:max_files]]

        proxy_config = {"useApifyProxy": False}
        if USE_RESIDENTIAL_PROXY:
            proxy_config = {
                "useApifyProxy": True,
                "apifyProxyGroups": ["RESIDENTIAL"],
            }
            if PROXY_COUNTRY:
                proxy_config["apifyProxyCountry"] = PROXY_COUNTRY

        run_input = {
            "URLItem": url_items,
            "uniqueFileNames": True,
            "proxyConfig": proxy_config,
        }

        run = await self.client.actor(ACTOR_ID).start(run_input=run_input)
        run_id = getattr(run, "id", None) or (run.get("id") if isinstance(run, dict) else None)
        logger.info(f"Started Actor run: {run_id} (proxy={proxy_config})")
        return run

    async def wait_for_run(self, run_id: str, timeout_secs: int = 1800):
        try:
            run = await self.client.run(run_id).wait_for_finish(
                wait_duration=timedelta(seconds=timeout_secs)
            )
        except TypeError:
            run = await self.client.run(run_id).wait_for_finish(wait_secs=timeout_secs)
        return run

    async def get_run_info(self, run_id: str):
        return await self.client.run(run_id).get()

    async def get_dataset_items(self, dataset_id: str) -> list:
        items = []
        async for item in self.client.dataset(dataset_id).iterate_items():
            if not isinstance(item, dict):
                try:
                    item = dict(item)
                except Exception:
                    item = getattr(item, "__dict__", {}) or {}
            items.append(item)
        return items

    async def get_dataset_summary(self, dataset_id: str) -> dict:
        items = await self.get_dataset_items(dataset_id)

        def is_success(i):
            status = str(i.get("status") or i.get("state") or "").lower()
            if status in ("success", "succeeded", "ok", "downloaded", "complete"):
                return True
            if i.get("success") is True:
                return True
            if i.get("error") or i.get("errorMessage") or i.get("failReason"):
                return False
            if status in ("failed", "error", "fail"):
                return False
            if i.get("downloadUrl") or i.get("fileUrl") or i.get("storageUrl") or i.get("publicUrl"):
                return True
            if (i.get("size") or i.get("fileSize") or i.get("sizeBytes")) and not i.get("error"):
                return True
            return False

        success = [i for i in items if is_success(i)]
        failed = [i for i in items if not is_success(i)]

        reasons = []
        for i in failed[:8]:
            reason = (
                i.get("error") or i.get("errorMessage") or i.get("failReason")
                or i.get("message") or i.get("status") or "unknown"
            )
            url = i.get("url") or i.get("originalUrl") or i.get("sourceUrl") or ""
            reasons.append(f"• {str(url)[:55]} → {reason}")

        file_links = []
        for i in success:
            link = (
                i.get("downloadUrl") or i.get("fileUrl")
                or i.get("storageUrl") or i.get("publicUrl")
            )
            name = i.get("fileName") or i.get("filename") or i.get("name") or "file"
            size = i.get("size") or i.get("fileSize") or i.get("sizeBytes")
            if link:
                file_links.append({"name": name, "url": link, "size": size, "raw": i})

        return {
            "total": len(items),
            "success": len(success),
            "failed": len(failed),
            "items": items[:30],
            "reasons": reasons,
            "file_links": file_links,
        }

    async def download_file_from_url(self, url: str, local_path: str):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status != 200:
                        logger.warning(f"HTTP {resp.status} for {url}")
                        return None
                    async with aiofiles.open(local_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 64):
                            await f.write(chunk)
            return local_path
        except Exception as e:
            logger.error(f"download_file_from_url error: {e}")
            return None

    async def download_zip_from_store(self, store_id: str, local_path: str):
        try:
            possible_keys = [
                "OUTPUT.zip", "output.zip", "download.zip", "files.zip",
                "archive.zip", "result.zip",
            ]
            for key in possible_keys:
                try:
                    record = await self.client.key_value_store(store_id).get_record(key)
                    if record is None:
                        continue
                    content = getattr(record, "value", None) or (
                        record.get("value") if isinstance(record, dict) else None
                    )
                    if content and isinstance(content, (bytes, bytearray)) and len(content) > 100:
                        async with aiofiles.open(local_path, "wb") as f:
                            await f.write(content)
                        logger.info(f"Downloaded ZIP from key: {key}")
                        return local_path
                except Exception:
                    continue

            keys_result = await self.client.key_value_store(store_id).list_keys()
            items = (
                getattr(keys_result, "items", None)
                or (keys_result.get("items") if isinstance(keys_result, dict) else [])
                or []
            )
            for item in items:
                key = getattr(item, "key", None) or (item.get("key") if isinstance(item, dict) else "") or ""
                if key.lower().endswith(".zip"):
                    record = await self.client.key_value_store(store_id).get_record(key)
                    if not record:
                        continue
                    content = getattr(record, "value", None) or (
                        record.get("value") if isinstance(record, dict) else None
                    )
                    if content and isinstance(content, (bytes, bytearray)) and len(content) > 100:
                        async with aiofiles.open(local_path, "wb") as f:
                            await f.write(content)
                        return local_path
            return None
        except Exception as e:
            logger.error(f"Error downloading ZIP: {e}")
            return None
