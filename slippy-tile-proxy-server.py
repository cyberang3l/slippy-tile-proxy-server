#!/usr/bin/env python3

import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Tuple

from geonorge_provider import (
    GeonorgeCustomConfig,
    GeonorgeDatasetID,
    GeonorgeWMSDownloadProvider,
    default_CONCURRENT_GEONORGE_LARGE_TILE_DOWNLOADS
)
from nslock import getListOfActiveLocks
from providers import (
    BaseTileServerConfig,
    BaseTileSetConfig,
    ImageFileType,
    MainConfig,
    bcolors,
    printColor
)

# A brief explanation of the map configuration format of the tile proxy
# server follows.
#
# The map configuration must be defined in the mainConf dictionary.
# The mainConf is a dictionary, where the main keys are the map
# identifiers that the proxy expects in the GET requests that should
# look like this: http://localhost:8080/map_identifier/z/x/y (see the
# getTileSetConfFromUrl function below to see how the GET requests are
# handled and decoded). The value for each key is a BaseTileSetConfig
# that contains the tile layers download configuration.
# The main parameter required in the BaseTileSetConfig is the "tileServers".
# The "tileServers" contain a list of BaseTileServerConfig that defines
# the server urls and url format (urlFmt) where a tile layer can be
# downloaded from. Each BaseTileServerConfig defines a layer that will
# be composed before the slippy-tile-proxy can serve the tile.
#
# The order of BaseTileServerConfigs in the tileServers list
# matters, as the proxy server will download the tile layer from
# all the different definitions and create a tile composite.
# When creating the composite, the tile downloaded from the first
# BaseTileServerConfig (tileServers[0]) in the list is the base layer,
# whereas any subsequent layers are layers stacked on top of each other.
#
# The GET requests towards the proxy only support the slippy map format
# (https://wiki.openstreetmap.org/wiki/Slippy_map) where a zoom level
# 'z', a 'x' and a 'y' tile index is requested. If the server you want to
# download tiles from doesn't speak slippy map format (x/y/z) but you
# know how to write a function to translate x/y/z to whatever the remote
# server can understand, then you can create a configuration where
# where you define a python function called "dynGetTileUrl(z, x, y)"
# that is executed to find the url that will be used to download the
# given x/y/z tile and serve it to your application.
#
# Look at the 'norway_vfr' map definition below for an example of a
# map definition that uses the "dynGenTileUrl", and the "openflightmaps"
# for an example of a map definition that uses a base layer and an
# overlay. Start the server, and use the following links to see tile
# tile proxy server in action:
#
# http://localhost:8080/openflightmaps/8/136/92
# http://localhost:8080/norway_vfr/11/1066/566
# http://localhost:8080/norway_base_throttled/13/4288/2300
# http://localhost:8080/norway_overlay_throttled/13/4288/2300
# http://localhost:8080/norway_base_throttled/14/8576/4600
# http://localhost:8080/norway_overlay_throttled/14/8576/4600
# http://localhost:8080/norway_contours_only_throttled/11/1066/566

mainConf = MainConfig(
    ktimatologio=BaseTileSetConfig(
        tileServers=[
            BaseTileServerConfig(
                servers=[
                    "server.arcgisonline.com",
                    "services.arcgisonline.com",
                ],
                urlFmt="ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                headers={
                    'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0',
                    'Accept': '*/*',
                    'Referer': 'https://maps.ktimatologio.gr/',
                    'Origin': 'https://maps.ktimatologio.gr',
                    'DNT': '1',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'cross-site',
                    'Connection': 'keep-alive',
                    'Accept-Encoding': 'gzip, deflate, br, zstd',
                    'TE': 'trailers',
                }
            ),
        ],
    ),
    opentopomap=BaseTileSetConfig(
        tileServers=[
            BaseTileServerConfig(
                servers=[
                    "a.tile.opentopomap.org",
                    "b.tile.opentopomap.org",
                    "c.tile.opentopomap.org",
                ],
                urlFmt="{z}/{x}/{y}.png",
            ),
        ],
    ),
    openflightmaps=BaseTileSetConfig(
        tileServers=[
            BaseTileServerConfig(
                servers=[
                    "nwy-tiles-api.prod.newaydata.com",
                ],
                urlFmt="tiles/{z}/{x}/{y}.jpg?path=latest/base/latest",
            ),
            BaseTileServerConfig(
                servers=[
                    "nwy-tiles-api.prod.newaydata.com",
                ],
                urlFmt="tiles/{z}/{x}/{y}.png?path=latest/aero/latest",
            ),
        ],
    ),
    openflighttopo=BaseTileSetConfig(
        tileServers=[
            BaseTileServerConfig(
                servers=[
                    "a.tile.opentopomap.org",
                    "b.tile.opentopomap.org",
                    "c.tile.opentopomap.org",
                ],
                urlFmt="{z}/{x}/{y}.png",
            ),
            BaseTileServerConfig(
                servers=[
                    "nwy-tiles-api.prod.newaydata.com",
                ],
                urlFmt="tiles/{z}/{x}/{y}.png?path=latest/aero/latest",
            ),
        ],
    ),
    norway_vfr=BaseTileSetConfig(
        tileServers=[
            BaseTileServerConfig(
                dynUrl=True,
                servers=[
                    """
import urllib
import mercantile
def dynGetTileUrl(z, x, y):
    url = 'https://avigis.avinor.no/agsmap/rest/services/ICAO_500000/MapServer/export?'
    # Use mercantile to find the bounding box
    bbox = mercantile.bounds(x, y, z)
    params = {
        "f": "image",
        "format": "png32",
        "transparent": "true",
        "layers": "show:3",
        "bbox": "{},{},{},{}".format(bbox.west, bbox.south, bbox.east, bbox.north),
        "bboxSR": 4326, # WGS 84: https://developers.arcgis.com/rest/services-reference/enterprise/export-image.htm
        # Web Mercator (3857):
        # https://developers.arcgis.com/rest/services-reference/enterprise/export-image.htm
        "imageSR": 3857,
        "size": "256,256",
    }
    return url + urllib.parse.urlencode(params)
""",
                ],
            ),
        ],
    ),
    norway_base_throttled=BaseTileSetConfig(
        downloader=GeonorgeWMSDownloadProvider(),
        tileServers=[
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA_GRAY,
                    tileLayerName="Hoydelag"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA_GRAY,
                    tileLayerName="Arealdekkeflate"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA_GRAY,
                    tileLayerName="Vannflate"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_FJELLSKYGGE,
                    tileLayerName="fjellskygge"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_elver"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_verneomradegrense"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA_GRAY,
                    tileLayerName="Hoydekurver"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_vannkontur"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA_GRAY,
                    tileLayerName="Vannkontur"
                ),
                servers=[],
            ),
        ],
    ),
    norway_contours_only_throttled=BaseTileSetConfig(
        downloader=GeonorgeWMSDownloadProvider(),
        tileServers=[
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_elver"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_verneomradegrense"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA_GRAY,
                    tileLayerName="Hoydekurver"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_vannkontur"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA_GRAY,
                    tileLayerName="Vannkontur"
                ),
                servers=[],
            ),
        ],
    ),
    norway_overlay_throttled=BaseTileSetConfig(
        downloader=GeonorgeWMSDownloadProvider(),
        tileServers=[
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_jernbane"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_veger"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_ferger"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_anleggslinjer"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_tettsted"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_jernbanestasjon"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_bygninger"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_bygningspunkt"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_arealdekkepunkt"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_turisthytte"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_hoydepunkt"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_vegnavn"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_stedsnavn"
                ),
                servers=[],
            ),
        ],
    ),
    norway_base_colored=BaseTileSetConfig(
        downloader=GeonorgeWMSDownloadProvider(),
        tileServers=[
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_hoydelag"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_arealdekkeflate"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_vannflate"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_FJELLSKYGGE,
                    tileLayerName="fjellskygge"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_elver"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_verneomradegrense"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA_GRAY,
                    tileLayerName="Hoydekurver"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA,
                    tileLayerName="kd_vannkontur"
                ),
                servers=[],
            ),
            BaseTileServerConfig(
                tileCacheTimeoutSec=86400 * 28,
                customConfig=GeonorgeCustomConfig(
                    wmsDataset=GeonorgeDatasetID.WMS_KARTDATA_GRAY,
                    tileLayerName="Vannkontur"
                ),
                servers=[],
            ),
        ],
    ),
)

hostName = os.environ.get("BIND_ADDR", "0.0.0.0")
serverPort = int(os.environ.get("BIND_PORT", 8080))


class HttpRequestHandler(BaseHTTPRequestHandler):
    def getTileSetConfFromUrl(self) -> Tuple[int, int, int, BaseTileSetConfig]:
        # Always expect a url in the form of /map_identifier/z/x/y
        data = self.path.rstrip('/').lstrip('/').split('/')
        if len(data) < 4:
            raise ValueError(
                "Error: expecting GET request in the form 'map_config/z/x/y'")

        mapId = data[0]
        mapConf = mainConf.get(mapId, None)
        if mapConf is None:
            raise IndexError(
                f"Error: no map '{data[0]}' found in the tile proxy conf")

        z = int(data[1])
        x = int(data[2])
        y = int(data[3])

        return z, x, y, mapId, mapConf

    def parseFirstLevelPaths(self, path: str) -> bool:
        if path == "/favicon.ico":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b'')
            return True
        elif path == "/locks":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(
                getListOfActiveLocks(
                    return_str=True,
                    sorted_by_refcount=False).encode())
            return True
        elif path == "/locks-sorted":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(
                getListOfActiveLocks(
                    return_str=True,
                    sorted_by_refcount=True).encode())
            return True
        elif path == "/settings":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()

            concurrentGeonorgeLargeDownloads = str(os.environ.get(
                "CONCURRENT_GEONORGE_LARGE_TILE_DOWNLOADS",
                default_CONCURRENT_GEONORGE_LARGE_TILE_DOWNLOADS))
            self.wfile.write(f"CONCURRENT_GEONORGE_LARGE_TILE_DOWNLOADS={concurrentGeonorgeLargeDownloads}".encode())
            return True
        # Request hasn't been served yet - return False
        return False

    def do_GET(self):
        printColor(
            f" - Serving Incoming request {bcolors.BOLD}{self.path}",
            color=bcolors.PURPLE)
        if self.parseFirstLevelPaths(path=self.path):
            return

        try:
            z, x, y, mapId, mapConf = self.getTileSetConfFromUrl()

            image = mapConf.downloader.downloadTile(z, x, y, mapId, mapConf)
            image_type = image.format
            image_blob = (
                image.make_blob() if (
                    mapConf.filetype == ImageFileType.AUTO
                ) else image.make_blob(mapConf.filetype)
            )

            self.send_response(200)
            self.send_header("Content-type", f"image/{image_type}")
            self.end_headers()
            printColor(
                f" - Serving tile {self.path}",
                color=bcolors.BOLD + bcolors.BLUE)
            self.wfile.write(image_blob)
        except BrokenPipeError:
            printColor(
                "Broken pipe - won't respond to the client",
                color=bcolors.RED)
        except BaseException:
            printColor(traceback.format_exc(), color=bcolors.RED)
            self.send_error(408)


if __name__ == "__main__":
    webServer = ThreadingHTTPServer(
        (hostName, serverPort), HttpRequestHandler)
    printColor(
        f"Tile proxy started http://{hostName}:{serverPort}",
        color=bcolors.WHITE)
    printColor("Serving layers:", color=bcolors.WHITE)
    for map_key in mainConf.keys():
        printColor(
            f"* http://{hostName}:{serverPort}/{map_key}/z/x/y",
            color=bcolors.WHITE)

    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        pass

    webServer.server_close()

    printColor("Server stopped.", color=bcolors.WHITE)
