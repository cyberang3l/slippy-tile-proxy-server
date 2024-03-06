import enum
import hashlib
import os
import sys
import time
import urllib
from threading import Lock
from typing import List, NamedTuple, Optional, Tuple

import mercantile
import pyproj
import wand
from wand.exceptions import OptionError
from wand.image import Image

from providers import (BaseDownloadProvider, BaseTileServerConfig,
                       BaseTileSetConfig, bcolors, buildCompositeImage,
                       printColor)

_downloadLock = Lock()
_cacheLock = Lock()


class GeonorgeDatasetID(str, enum.Enum):
    WMS_KARTDATA = "https://openwms.statkart.no/skwms1/wms.kartdata?"
    WMS_KARTDATA_GRAY = "https://openwms.statkart.no/skwms1/wms.kartdata3graatone?"
    WMS_FJELLSKYGGE = "https://openwms.statkart.no/skwms1/wms.fjellskygge?"


class GeonorgeCustomConfig(NamedTuple):
    wmsDataset: GeonorgeDatasetID
    tileLayerName: str
    dpi: int = 192
    sizePx: int = 512


class GeonorgeWMSDownloadProvider(BaseDownloadProvider):
    """
    The WMS server of Geonorge (statkart) throttles requests aggressively.
    The GeonorgeWMSDownloadProvider reduces the number of requests towards
    the WMS server by requesting larger extents (8x8 tile extents) of individual
    layers, caches the layers and then makes composites and crops them into
    tiles that can be served by the tile proxy server. The composites are
    also cached independently (two-level cache).
    This approach has several benefits:
    1. We reduce the number of requests towards the WMS dramatically and we
       are not getting throttled often, but we still download large layers that
       can server 64 tiles per request. In fact, the thread that downloads
       the layers has a thread lock (so only one concurrent request is allowed
       to the server), and I don't see the dreaded "overuse - wait a little"
       message anymore.
    2. Since we cache each individual layer, we can make different combinations
       of maps configs, and any layer that has been downloaded will be reused
       from the cache even across different slippy-tile-proxy-server map
       configurations.
    3. Small tiles that have been cropped are cached and associated with
       individual map configs. If we request them again, they are served
       immediately. No need to read a bunch of large layers and re-generated
       the composites
    """

    def __init__(self, downloadTimeoutSec: int = 20):
        self._downloadTimeoutSec = downloadTimeoutSec
        super(GeonorgeWMSDownloadProvider, self).__init__()

    @property
    def downloadTimeoutSec(self) -> int:
        return self._downloadTimeoutSec

    @downloadTimeoutSec.setter
    def downloadTimeoutSec(self, downloadTimeoutSec: int):
        self._downloadTimeoutSec = downloadTimeoutSec

    def _getXYWH(self, z: int, x: int, y: int,
                 sizePx: int) -> Tuple[
            int, int, int, int, int, int, int]:
        tilesToRequest = 8 if 2 ** z >= 8 else 2 ** z

        x = x - (x % tilesToRequest)
        y = y - (y % tilesToRequest)
        x2 = x + tilesToRequest - 1
        y2 = y + tilesToRequest - 1

        width = sizePx * ((x2 - x) + 1)
        height = sizePx * ((y2 - y) + 1)

        return x, y, x2, y2, width, height, tilesToRequest

    def _getTileLayerCachePath(
        self,
        dataset: GeonorgeDatasetID, layer: str,
            z: int, x: int, y: int,
            dpi: int, baseTileSizePx: int) -> str:

        x, y, _, _, width, height, tilesToRequest = self._getXYWH(
            z, x, y, baseTileSizePx)

        cacheDir = os.path.join(
            self._tileCacheBasePath, dataset.name, layer, str(z), str(x))

        os.makedirs(cacheDir, exist_ok=True)
        cacheFile = os.path.join(
            cacheDir,
            f"{y}_{tilesToRequest}x{tilesToRequest}_{baseTileSizePx}px_base_{dpi}dpi_{width}x{height}px.png")

        return cacheFile

    def getTileLayerCachePath(self, z: int, x: int, y: int,
                              mapId: str,
                              tileConf: 'BaseTileSetConfig',
                              tileServerConf: BaseTileServerConfig) -> str:
        dataset = tileServerConf.customConfig.wmsDataset
        layer = tileServerConf.customConfig.tileLayerName
        dpi = tileServerConf.customConfig.dpi
        sizePx = tileServerConf.customConfig.sizePx
        return self._getTileLayerCachePath(
            dataset, layer, z, x, y, dpi, sizePx)

    def _downloadSingleTileLayer(self, url: str) -> Image:
        def downloadSingleTileLayer(url: str):
            printColor(
                f"Downloading tile layer from url: {url}",
                color=bcolors.BLUE)
            with urllib.request.urlopen(url, timeout=self._downloadTimeoutSec) as conn:
                return conn.read()

        # Do only one download per second from WMS Geonorge to
        # limit the throttling
        maxRetries = 10
        numRetries = 0
        while True:
            if numRetries >= maxRetries:
                raise BaseException(
                    f"reached max retries ({maxRetries}) and failed to download {url}")

            numRetries += 1
            data = downloadSingleTileLayer(url)
            try:
                # Try to parse and return the data as an image - if it fails,
                # retry to download the same data in case we got throttled
                # by the remote server
                return Image(blob=data)
            except OptionError as e:
                # This error checking chunk is specific to the geonorge WMS server
                # that throttles requests aggressively, but returns a 200
                # response.
                printColor("Error occured:", e, color=bcolors.RED)
                msg = data.decode('ISO-8859-1')
                if "Overforbruk" in msg:
                    printColor(
                        "Overuse error - Sleeping 1 second and retrying",
                        color=bcolors.BROWN)
                    time.sleep(1)
                    continue
                else:
                    printColor(
                        "Success but not valid image returned - will not retry this one",
                        color=bcolors.BOLD + bcolors.YELLOW)

    def _getLayerFromGeonorge(
            self,
            z: int, x: int, y: int,
            mapId: str,
            tileConf: 'BaseTileSetConfig',
            tileServerConf: BaseTileServerConfig) -> Image:

        with _downloadLock:
            cachedImage, cachePath = self.getTileLayerFromCache(
                z, x, y, mapId, tileConf, tileServerConf)
            if cachedImage:
                printColor(
                    f"Loading tile layer from cache: {cachePath}",
                    color=bcolors.WHITE)
                return cachedImage

            dataset = tileServerConf.customConfig.wmsDataset
            layer = tileServerConf.customConfig.tileLayerName
            dpi = tileServerConf.customConfig.dpi
            sizePx = tileServerConf.customConfig.sizePx
            url = dataset.value

            x, y, x2, y2, width, height, tilesToRequest = self._getXYWH(
                z, x, y, sizePx)

            transformer = pyproj.Transformer.from_crs("WGS84", "EPSG:3857")
            bbox1 = mercantile.bounds(x, y, z)
            bbox2 = mercantile.bounds(x2, y2, z)
            south, west, north, east = transformer.transform_bounds(
                bbox2.south, bbox1.west, bbox1.north, bbox2.east)

            params = {
                "SERVICE": "WMS",
                "VERSION": "1.3.0",
                "REQUEST": "GetMap",
                "BBOX": "{},{},{},{}".format(south, west, north, east),
                "CRS": "EPSG:3857",
                "WIDTH": width,
                "HEIGHT": height,
                "LAYERS": layer,
                "FORMAT": "image/png",
                "DPI": dpi,
                "MAP_RESOLUTION": dpi,
                "STYLE": "default",
                "TRANSPARENT": "true"
            }

            image = self._downloadSingleTileLayer(
                url + urllib.parse.urlencode(params))

            if tileServerConf.enableTileCache:
                # Cache the downloaded file if the cache for this layer is
                # enabled
                cachePath = self.getTileLayerCachePath(
                    z, x, y, mapId, tileConf, tileServerConf)
                print(
                    f"Saving tile layer in cache: {cachePath}",
                    file=sys.stderr)
                with _cacheLock:
                    image.save(filename=cachePath)

            return image

    def _makeCompositeFromLayers(self, layers: List[Image]) -> Image:
        base = layers[0]
        if len(layers) == 1:
            return base

        for i in range(1, len(layers)):
            overlay = layers[i]
            base = buildCompositeImage(base, overlay)

        return base

    def _getTileCompositeCachePath(self, z: int, x: int, y: int, mapId: str,
                                   tileConf: 'BaseTileSetConfig') -> str:
        tileLayerNames = []
        for layerIdx, tileServer in enumerate(tileConf.tileServers):
            tileLayerNames.append(tileServer.customConfig.tileLayerName)
        strToHash = "/".join(tileLayerNames)
        hashCalc = hashlib.blake2b(digest_size=8)
        hashCalc.update(strToHash.encode())
        cacheDir = os.path.join(
            self._tileCacheBasePath, mapId,
            hashCalc.hexdigest(),
            str(z), str(x)
        )
        os.makedirs(cacheDir, exist_ok=True)
        cacheFile = os.path.join(cacheDir, str(y))
        return cacheFile

    def _getTileCompositeFromCache(
            self, z: int, x: int, y: int,
            mapId: str,
            tileConf: 'BaseTileSetConfig') -> Tuple[Optional[Image], Optional[str]]:
        """
        Geonorge layers are large and many, so it takes time to
        make composites and split them every time. So cache the
        composite tiles too.
        """
        minTileCacheTimeoutSec = -1
        for layerIdx, tileServer in enumerate(tileConf.tileServers):
            if tileServer.enableTileCache:
                if layerIdx == 0:
                    minTileCacheTimeoutSec = tileServer.tileCacheTimeoutSec
                else:
                    minTileCacheTimeoutSec = min(
                        minTileCacheTimeoutSec, tileServer.tileCacheTimeoutSec)

        path = self._getTileCompositeCachePath(z, x, y, mapId, tileConf)
        try:
            lastModTime = os.path.getmtime(path)
            if time.time() - lastModTime > minTileCacheTimeoutSec:
                printColor(
                    f"Cache expired for composite tile {path}",
                    color=bcolors.YELLOW)
                return None, None
        except FileNotFoundError:
            return None, None

        try:
            with _cacheLock:
                return Image(filename=path), path
        except wand.exceptions.BlobError:
            pass
        return None, None

    def _cropLargeCompositeAndCacheIt(
            self, image: Image, sizePx: int,
            z: int, xReq: int, yReq: int,
            mapId: str, tileConf: 'BaseTileSetConfig') -> Image:
        topLeftX, topLeftY, _, _, _, _, gridSize = self._getXYWH(
            z, xReq, yReq, sizePx)
        retTile = None
        for xi in range(gridSize):
            for yi in range(gridSize):
                x = xi + topLeftX
                y = yi + topLeftY
                cachePath = self._getTileCompositeCachePath(
                    z, x, y, mapId, tileConf)
                print(f"Cropping and caching {cachePath}", file=sys.stderr)
                left = xi * sizePx
                right = left + sizePx
                top = yi * sizePx
                bottom = top + sizePx
                with image[left:right, top:bottom] as crop:
                    with _cacheLock:
                        crop.save(filename=cachePath)
                    if x == xReq and y == yReq:
                        retTile = crop[:]
        return retTile

    def downloadTile(self, z: int, x: int, y: int,
                     mapId: str,
                     tileConf: 'BaseTileSetConfig') -> Image:
        tile, tileCachePath = self._getTileCompositeFromCache(
            z, x, y, mapId, tileConf)
        if tile:
            printColor(
                f"Tile fetched from cache: {tileCachePath}",
                color=bcolors.GREEN)
            return tile

        sizePx = 0
        dpi = 0
        for layerIdx, tileServerConf in enumerate(tileConf.tileServers):
            if layerIdx == 0:
                dpi = tileServerConf.customConfig.dpi
                sizePx = tileServerConf.customConfig.sizePx
            else:
                layerName = tileServerConf.customConfig.tileLayerName
                layerDpi = tileServerConf.customConfig.dpi
                layerSize = tileServerConf.customConfig.sizePx
                if layerDpi != dpi or layerSize != sizePx:
                    raise BaseException(
                        f"Layer {layerName} has a different dpi/sizePx ({layerDpi}/{layerSize}) from the previous layers ({dpi}/{sizePx})")

        downloadedLayers = []
        for tileServerConf in tileConf.tileServers:
            layer = self._getLayerFromGeonorge(
                z, x, y, mapId, tileConf, tileServerConf)
            downloadedLayers.append(layer)

        compositeTile = self._makeCompositeFromLayers(downloadedLayers)

        tile = self._cropLargeCompositeAndCacheIt(
            compositeTile, sizePx, z, x, y, mapId, tileConf)
        return tile
