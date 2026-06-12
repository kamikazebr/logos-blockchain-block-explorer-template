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


class HttpNodeApi(NodeApi):
    # Paths can't have a leading slash since they are relative to the base URL
    ENDPOINT_INFO = "cryptarchia/info"
    ENDPOINT_BLOCKS_STREAM = "cryptarchia/events/blocks/stream"
    ENDPOINT_BLOCK_BY_HASH = "cryptarchia/blocks/"  # block hash appended as path segment

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
            return HealthSerializer.from_healthy()
        else:
            return HealthSerializer.from_unhealthy()

    async def get_info(self) -> InfoSerializer:
        url = urljoin(self.base_url, self.ENDPOINT_INFO)
        response = requests.get(url, auth=self.authentication, timeout=60)
        response.raise_for_status()
        return InfoSerializer.model_validate(normalize_info_payload(response.json()))

    async def get_block_by_hash(self, block_hash: str) -> Optional[BlockSerializer]:
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

    async def get_blocks_stream(self) -> AsyncIterator[BlockSerializer]:
        url = urljoin(self.base_url, self.ENDPOINT_BLOCKS_STREAM)
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
                        event = json.loads(line)
                        block = BlockSerializer.model_validate(event["block"])
                    except (ValidationError, KeyError, json.JSONDecodeError) as error:
                        logger.exception(error)
                        continue

                    logger.debug(f"Received new block from Node: {block}")
                    yield block
