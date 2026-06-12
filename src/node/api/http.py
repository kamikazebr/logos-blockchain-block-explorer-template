import json
import logging
from typing import TYPE_CHECKING, AsyncIterator, Optional
from urllib.parse import urljoin, urlunparse

import httpx
from pydantic import ValidationError
from rusty_results import Empty, Option, Some
from third_party import requests

from core.authentication import Authentication
from node.api.base import NodeApi
from node.api.serializers.block import BlockSerializer
from node.api.serializers.health import HealthSerializer
from node.api.serializers.info import InfoSerializer

if TYPE_CHECKING:
    from core.app import NBESettings


logger = logging.getLogger(__name__)


def normalize_info_payload(data: dict) -> dict:
    """Normalize cryptarchia/info responses across node versions.

    Older nodes (<= 0.1.2) return a flat object with mode as a string
    ("Online"). Newer nodes wrap the fields under "cryptarchia_info" and report
    mode as a (possibly nested) object like {"Started": "Online"}.
    """
    info = dict(data.get("cryptarchia_info", data))
    mode = data.get("mode", info.get("mode"))
    while isinstance(mode, dict):
        mode = next(iter(mode.values()), "Unknown") if mode else "Unknown"
    info["mode"] = mode if isinstance(mode, str) else str(mode)
    return info


def adapt_storage_block_payload(payload: dict, block_hash: str) -> dict:
    """Adapt a legacy POST storage/block response to the BlockSerializer shape.

    Unlike GET cryptarchia/blocks/<hash>, storage/block does not echo the
    block's own id in the header, so inject the hash that was requested.
    """
    header = dict(payload.get("header", {}))
    header.setdefault("id", block_hash)
    return {**payload, "header": header}


class HttpNodeApi(NodeApi):
    # Paths can't have a leading slash since they are relative to the base URL
    ENDPOINT_INFO = "cryptarchia/info"
    ENDPOINT_BLOCKS_STREAM = "cryptarchia/events/blocks/stream"
    ENDPOINT_BLOCK_BY_HASH = "cryptarchia/blocks/"  # block hash appended as path segment
    # Legacy (<= 0.1.2) endpoints, used as fallbacks when the modern ones are absent.
    ENDPOINT_STORAGE_BLOCK = "storage/block"  # POST, body is the block hash as a JSON string
    ENDPOINT_LIB_STREAM = "cryptarchia/lib-stream"  # SSE of finalized {height, header_id}

    # Node API generations (which block endpoints the node serves).
    GENERATION_MODERN = "modern"  # GET blocks/<hash> + blocks event stream
    GENERATION_LEGACY = "legacy (<= 0.1.2)"  # POST storage/block + lib-stream

    def __init__(self, settings: "NBESettings"):
        self.host: str = settings.node_api_host
        self.port: int = settings.node_api_port
        self.protocol: str = settings.node_api_protocol or "http"
        self.timeout: int = settings.node_api_timeout or 60
        self.authentication: Option[Authentication] = (
            Some(settings.node_api_auth) if settings.node_api_auth else Empty()
        )
        auth = self.authentication.map(lambda _auth: _auth.for_httpx()).unwrap_or(None)
        self._client = httpx.AsyncClient(timeout=self.timeout, auth=auth)
        self._generation: Optional[str] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def base_url(self) -> str:
        if "/" in self.host:
            host, path = self.host.split("/", 1)
            path = f"/{path}"
            if not path.endswith("/"):
                path += "/"
        else:
            host = self.host
            path = ""

        network_location = f"{host}:{self.port}" if self.port else host

        url = urlunparse(
            (
                self.protocol,
                network_location,
                path,
                # The following are unused but required
                "",  # Params
                "",  # Query
                "",  # Fragment
            )
        )
        return url

    async def get_health(self) -> HealthSerializer:
        url = urljoin(self.base_url, self.ENDPOINT_INFO)
        response = requests.get(url, auth=self.authentication, timeout=60)
        if response.status_code == 200:
            return HealthSerializer.from_healthy(node_api=self._generation)
        else:
            return HealthSerializer.from_unhealthy(node_api=self._generation)

    async def get_info(self) -> InfoSerializer:
        url = urljoin(self.base_url, self.ENDPOINT_INFO)
        response = requests.get(url, auth=self.authentication, timeout=60)
        response.raise_for_status()
        return InfoSerializer.model_validate(normalize_info_payload(response.json()))

    async def get_generation(self) -> str:
        """Detect (once) which block endpoints this node serves.

        Modern nodes serve GET cryptarchia/blocks/<hash> and the blocks event
        stream; legacy (<= 0.1.2) nodes serve POST storage/block and lib-stream
        instead. Probed with the node's own LIB hash, which is always stored.
        """
        if self._generation is not None:
            return self._generation

        info = await self.get_info()
        modern = await self._client.get(urljoin(self.base_url, self.ENDPOINT_BLOCK_BY_HASH + info.lib))
        if modern.status_code == 200:
            self._generation = self.GENERATION_MODERN
        else:
            legacy = await self._client.post(urljoin(self.base_url, self.ENDPOINT_STORAGE_BLOCK), json=info.lib)
            if legacy.status_code == 200 and legacy.json() is not None:
                self._generation = self.GENERATION_LEGACY
            else:
                raise RuntimeError(
                    f"Node at {self.base_url} serves neither GET {self.ENDPOINT_BLOCK_BY_HASH}<hash> "
                    f"(HTTP {modern.status_code}) nor POST {self.ENDPOINT_STORAGE_BLOCK} "
                    f"(HTTP {legacy.status_code}); cannot fetch blocks."
                )
        logger.info(f"Detected node API generation: {self._generation}")
        return self._generation

    async def get_block_by_hash(self, block_hash: str) -> Optional[BlockSerializer]:
        if await self.get_generation() == self.GENERATION_LEGACY:
            return await self._get_block_legacy(block_hash)
        return await self._get_block_modern(block_hash)

    async def _get_block_modern(self, block_hash: str) -> Optional[BlockSerializer]:
        url = urljoin(self.base_url, self.ENDPOINT_BLOCK_BY_HASH + block_hash)
        response = await self._client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        json_data = response.json()
        if json_data is None:
            logger.warning(f"Block {block_hash} returned null from API")
            return None
        return BlockSerializer.model_validate(json_data)

    async def _get_block_legacy(self, block_hash: str) -> Optional[BlockSerializer]:
        url = urljoin(self.base_url, self.ENDPOINT_STORAGE_BLOCK)
        response = await self._client.post(url, json=block_hash)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        json_data = response.json()
        if json_data is None:
            return None
        return BlockSerializer.model_validate(adapt_storage_block_payload(json_data, block_hash))

    async def get_blocks_stream(self) -> AsyncIterator[BlockSerializer]:
        if await self.get_generation() == self.GENERATION_LEGACY:
            stream = self._get_blocks_stream_legacy()
        else:
            stream = self._get_blocks_stream_modern()
        async for block in stream:
            yield block

    async def _get_blocks_stream_modern(self) -> AsyncIterator[BlockSerializer]:
        url = urljoin(self.base_url, self.ENDPOINT_BLOCKS_STREAM)
        async for event in self._stream_json_lines(url):
            try:
                block = BlockSerializer.model_validate(event["block"])
            except (ValidationError, KeyError) as error:
                logger.exception(error)
                continue
            logger.debug(f"Received new block from Node: {block}")
            yield block

    async def _get_blocks_stream_legacy(self) -> AsyncIterator[BlockSerializer]:
        # Legacy nodes only stream finalized header ids ({height, header_id});
        # fetch each block body from storage.
        url = urljoin(self.base_url, self.ENDPOINT_LIB_STREAM)
        async for event in self._stream_json_lines(url):
            header_id = event.get("header_id")
            if not header_id:
                logger.warning(f"lib-stream event without header_id: {event}")
                continue
            try:
                block = await self._get_block_legacy(header_id)
            except (ValidationError, httpx.HTTPError) as error:
                logger.exception(error)
                continue
            if block is None:
                logger.warning(f"Finalized block {header_id} not found in node storage")
                continue
            logger.debug(f"Received new block from Node (lib-stream): {block}")
            yield block

    async def _stream_json_lines(self, url: str) -> AsyncIterator[dict]:
        auth = self.authentication.map(lambda _auth: _auth.for_httpx()).unwrap_or(None)
        # Use no read timeout for streaming - blocks may arrive infrequently
        stream_timeout = httpx.Timeout(connect=self.timeout, read=None, write=self.timeout, pool=self.timeout)
        async with httpx.AsyncClient(timeout=stream_timeout, auth=auth) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()  # TODO: Result

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as error:
                        logger.exception(error)
                        continue
