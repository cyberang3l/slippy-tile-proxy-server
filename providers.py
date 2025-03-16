import concurrent.futures
import hashlib
import os
import sys
import time
import urllib.request
from abc import ABC, abstractmethod
from enum import Enum, StrEnum
from pathlib import Path
from random import randint
from typing import (
    Any,
    Dict,
    List,
    NamedTuple,
    Optional,
    Tuple,
    Union,
    overload
)

import wand
from wand.image import Image

from nslock import NamespaceLock

_POSIX_PROG_NAME = "slippy-tile-proxy"


class bcolors(StrEnum):
    PURPLE = '\033[95m'
    BLUE = '\033[94m'
    WHITE = '\033[97m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    BROWN = '\033[33m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def pstderr(*args):
    print(*args, file=sys.stderr)


def printColor(*args, color: bcolors = bcolors.ENDC):
    pstderr(color, *args, bcolors.ENDC)


def buildCompositeImage(base: Image, overlay: Image) -> Image:
    # Compose a base image and an overlay, and return the
    # generated PNG image

    # Enforce base and overlay to be a PNG - if not, and the base is
    # a jpg image, image magick will assume the base format as the default
    base.format = "png"
    overlay.format = "png"

    bw, bh = base.size
    ow, oh = overlay.size

    # Resize base or overlay to the width/height of the smallest layer
    if bw != ow:
        if bw > ow:
            base.resize(ow, oh)
        else:
            overlay.resize(bw, bh)

    base.composite(overlay)
    return base


class TileServerProtocol(str, Enum):
    HTTP = "http"
    HTTPS = "https"


class ImageFileType(str, Enum):
    PNG = "png"
    JPG = "jpeg"
    AUTO = "auto"


Key = str
Value = str


class BaseTileServerConfig(NamedTuple):
    # A list of servers that we will alternate when downloading tiles
    servers: List[str]
    # A string in the form of 'layer/{z}/{x}/{y}' (expect to contain the {z},
    # {x}, and {y} placeholders
    urlFmt: str = "{z}/{x}/{y}"
    protocol: TileServerProtocol = TileServerProtocol.HTTPS
    enableTileCache: bool = True
    tileCacheTimeoutSec: int = 172800
    # Optional url request headers to use for each request to the tile servers
    headers: Optional[Dict[Key, Value]] = None
    # If the server you want to download tiles from doesn't speak the slippy
    # map format (x/y/z) but you know how to write a function to translate
    # x/y/z to whatever the remote server can understand, then you can set
    # the dynUrl option to True and define a python function called
    # "dynGetTileUrl(z, x, y)" as the first (and only) server in the `servers`
    # list. The "dynGetTileUrl" function will then be called to find the url
    # that will be used to download the given x/y/z tile and serve it to your
    # application.
    dynUrl: Optional[str] = None
    customConfig: Optional[Any] = None


class BaseDownloadProvider(ABC):
    def __init__(self):
        self._tileCacheBasePath = os.path.join(
            Path.home(), ".cache", _POSIX_PROG_NAME)
        os.makedirs(self._tileCacheBasePath, exist_ok=True)

    @abstractmethod
    def getTileLayerCachePath(self, z: int, x: int, y: int,
                              mapId: str,
                              tileConf: 'BaseTileSetConfig',
                              tileServerConf: BaseTileServerConfig) -> str:
        ...

    def getTileLayerFromCache(
            self, z: int, x: int, y: int,
            mapId: str,
            tileConf: 'BaseTileSetConfig',
            tileServerConf: BaseTileServerConfig) -> Tuple[Optional[Image], Optional[str]]:
        if tileServerConf.enableTileCache is False:
            return None, None

        path = self.getTileLayerCachePath(
            z, x, y, mapId, tileConf, tileServerConf)
        try:
            lastModTime = os.path.getmtime(path)
            if time.time() - lastModTime > tileServerConf.tileCacheTimeoutSec:
                printColor(
                    f"Cache expired for tile {path}",
                    color=bcolors.YELLOW)
                return None, None
        except FileNotFoundError:
            return None, None

        st = os.stat(path)
        if st.st_size == 0:
            printColor(
                f"Zero bytes file in cache ignored: {path} - may be corrupted",
                color=bcolors.YELLOW)
            return None, None

        try:
            with NamespaceLock(path):
                return Image(filename=path), path
        except wand.exceptions.BlobError:
            pass
        return None, None

    @abstractmethod
    def downloadTile(self, z: int, x: int, y: int,
                     mapId: str,
                     tileConf: 'BaseTileSetConfig') -> Image:
        ...

    def downloadTileBlob(self, z: int, x: int, y: int,
                         mapId: str,
                         tileConf: 'BaseTileSetConfig') -> bytes():
        image = self.downloadTile(z, x, y, mapId, tileConf)
        if tileConf.filetype == ImageFileType.AUTO:
            return image.make_blob()
        return image.make_blob(format=tileConf.filetype.value)


class MultithreadedDownloadProvider(BaseDownloadProvider):
    def __init__(
            self,
            numDownloadWorkers: int = 16,
            downloadTimeoutSec: int = 3):
        self._numDownloadWorkers = numDownloadWorkers
        self._downloadTimeoutSec = downloadTimeoutSec
        super(MultithreadedDownloadProvider, self).__init__()

    @property
    def numDownloadWorkers(self) -> int:
        return self._numDownloadWorkers

    @numDownloadWorkers.setter
    def numDownloadWorkers(self, numDownloadWorkers: int):
        self._numDownloadWorkers = numDownloadWorkers

    @property
    def downloadTimeoutSec(self) -> int:
        return self._downloadTimeoutSec

    @downloadTimeoutSec.setter
    def downloadTimeoutSec(self, downloadTimeoutSec: int):
        self._downloadTimeoutSec = downloadTimeoutSec

    def getTileLayerCachePath(self, z: int, x: int, y: int,
                              mapId: str,
                              tileConf: 'BaseTileSetConfig',
                              tileServerConf: BaseTileServerConfig) -> str:
        firstServer = tileServerConf.servers[0]
        urlFmt = tileServerConf.urlFmt
        hashCalc = hashlib.blake2b(digest_size=8)
        hashCalc.update(f"{firstServer}{urlFmt}".encode())
        cacheDir = os.path.join(
            self._tileCacheBasePath, mapId,
            hashCalc.hexdigest(),
            str(z), str(x)
        )
        os.makedirs(cacheDir, exist_ok=True)
        cacheFile = os.path.join(cacheDir, str(y))
        return cacheFile

    def _getTileUrlFromServerConf(self, z: int, x: int, y: int,
                                  mapId: str,
                                  tileConf: 'BaseTileSetConfig',
                                  tileServerConf: BaseTileServerConfig) -> str:
        # If "dynUrl" is True, a function named "dynGetTileUrl" that
        # takes as input params z, x, and y must be provided in the
        # tile_servers definition. This function will then be executed, and
        # the return value is the url that will be used to download the x/y tile
        # for zoom z.
        if tileServerConf.dynUrl is True:
            exec(tileServerConf.servers[0], globals())
            return dynGetTileUrl(z, x, y)  # noqa

        serverIdx = randint(0, len(tileServerConf.servers) - 1)
        return tileServerConf.protocol.value + "://" + tileServerConf.servers[
            serverIdx] + "/" + tileServerConf.urlFmt.format(z=z, x=x, y=y)

    def _downloadTileLayers(self, urls: Dict[int, str], headers: Dict[int, Optional[Dict[Key, Value]]]) -> Dict[
            int, Dict[str, Union[str, Image]]]:
        # Downloads tiles in parallel threads, and returns the download result in
        # a dict where the key is an integer. The key indicates the layering order
        # when making image composites later on, whereas the result with key 0 is
        # the base image, and any subsequent image is an overlay image

        def downloadSingleTileLayer(url: str, headers: Optional[Dict[Key, Value]]):
            # Downloads a tile from the url and returns the received bytes
            req = urllib.request.Request(url)
            if headers is not None:
                for k, v in headers.items():
                    req.add_header(k, v)

            with urllib.request.urlopen(req, timeout=self._downloadTimeoutSec) as conn:
                return conn.read()

        images = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self._numDownloadWorkers) as executor:
            # Start the download operations and mark each future with its URL
            futureToUrl = {
                executor.submit(
                    downloadSingleTileLayer,
                    url=url, headers=headers[urlIdx]): (url, urlIdx) for urlIdx, url in urls.items()}

            for future in concurrent.futures.as_completed(futureToUrl):
                url, urlIdx = futureToUrl[future]
                try:
                    data = future.result()
                except Exception as exc:
                    printColor(
                        f"{url} generated an exception: {exc}",
                        color=bcolors.RED)
                    return {}
                else:
                    printColor(
                        f"Downloaded {url} - {len(data)} bytes",
                        color=bcolors.CYAN)
                    images[urlIdx] = {"url": url, "image": Image(blob=data)}
        return images

    def _makeCompositeFromLayers(self, layers: List[Image]) -> Image:
        base = layers[0]
        if len(layers) == 1:
            return base

        for i in range(1, len(layers)):
            overlay = layers[i]
            base = buildCompositeImage(base, overlay)

        return base

    def _loadLayersFromCache(self, z: int, x: int, y: int,
                             mapId: str,
                             tileConf: 'BaseTileSetConfig') -> Dict[
            int, Dict[str, Union[str, Image]]]:
        cachedLayers = {}
        for layerIdx, tileServerConf in enumerate(tileConf.tileServers):
            if not tileServerConf.enableTileCache:
                continue
            layer, cachePath = self.getTileLayerFromCache(
                z, x, y, mapId, tileConf, tileServerConf)
            if layer is None:
                continue
            printColor(
                f"Loaded tile layer from cache: {cachePath}",
                color=bcolors.GREEN)
            cachedLayers[layerIdx] = {
                "url": cachePath, "image": layer}

        return cachedLayers

    def downloadTile(self, z: int, x: int, y: int,
                     mapId: str,
                     tileConf: 'BaseTileSetConfig') -> Image:
        cachedLayers = self._loadLayersFromCache(z, x, y, mapId, tileConf)

        urls: Dict[int, str] = {}
        headers: Dict[int, Optional[Dict[Key, Value]]] = {}
        for layerIdx, tileServerConf in enumerate(tileConf.tileServers):
            if layerIdx in cachedLayers.keys():
                # Picked this layer from the cache - no need to download
                continue

            url = self._getTileUrlFromServerConf(
                z, x, y, mapId, tileConf, tileServerConf)
            urls[layerIdx] = url
            headers[layerIdx] = tileServerConf.headers

        downloadedLayers = self._downloadTileLayers(urls, headers)
        for layerIdx, layer in downloadedLayers.items():
            tileServerConf = tileConf.tileServers[layerIdx]
            if tileServerConf.enableTileCache:
                # Cache the downloaded file if the cache for this layer is
                # enabled
                cachePath = self.getTileLayerCachePath(
                    z, x, y, mapId, tileConf, tileServerConf)
                print(
                    f"Saving tile layer in cache: {cachePath}",
                    file=sys.stderr)
                with NamespaceLock(cachePath):
                    layer["image"].save(filename=cachePath)

        allLayers = {**cachedLayers, **downloadedLayers}
        tile = self._makeCompositeFromLayers(
            [allLayers[i]["image"] for i in range(len(allLayers.keys()))])

        return tile


class BaseTileSetConfig(NamedTuple):
    tileServers: List[BaseTileServerConfig]
    filetype: ImageFileType = ImageFileType.AUTO
    downloader: BaseDownloadProvider = MultithreadedDownloadProvider()


class MainConfig(dict):
    @overload
    def __getitem__(self, name: str) -> BaseTileSetConfig:
        ...

    def __getitem__(self, name):
        return super().__getitem__(name)
