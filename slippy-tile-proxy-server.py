#!/usr/bin/env python3

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Tuple

from providers import (BaseTileServerConfig, BaseTileSetConfig, ImageFileType,
                       MainConfig)

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

mainConf = MainConfig(
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
)

hostName = "localhost"
serverPort = 8080


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

    def do_GET(self):
        print(" - Serving Incoming request " + self.path, file=sys.stderr)
        if self.path == "/favicon.ico":
            self.wfile.write(b'')
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
            self.wfile.write(image_blob)
        except BrokenPipeError:
            print("Broken pipe - won't respond to the client", file=sys.stderr)
        except BaseException as e:
            print("Error occured:", e, file=sys.stderr)
            self.send_error(408)


if __name__ == "__main__":
    webServer = ThreadingHTTPServer(
        (hostName, serverPort), HttpRequestHandler)
    print(
        f"Tile proxy started http://{hostName}:{serverPort}",
        file=sys.stderr)
    print("Serving layers:", file=sys.stderr)
    for map_key in mainConf.keys():
        print(
            f"* http://{hostName}:{serverPort}/{map_key}/z/x/y",
            file=sys.stderr)

    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        pass

    webServer.server_close()

    print("Server stopped.", file=sys.stderr)
