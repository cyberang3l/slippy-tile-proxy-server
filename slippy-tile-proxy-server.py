#!/usr/bin/env python3

import concurrent.futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mercantile
from random import randint
from typing import Dict, List, Union
import urllib
import urllib.request
from wand.image import Image


# A brief explanation of the map configuration format of the tile proxy
# server follows.
#
# The map configuration must be defined in the MAINCONFIG dictionary.
# The MAINCONFIG is a dictionary, where the main keys are the map
# identifiers that the proxy expects in the GET requests that should
# look like this: http://localhost:8080/map_identifier/z/x/y (see the
# get_tile_urls function below to see how the GET requests are handled
# and decoded). The value for each key is a python list that
# contains one or more dictionaries with a tile server configurations.
#
# When we have multiple dictionaries in the list, the definition
# order matters, as the proxy server will download the tile from
# all the different definitions and create a tile composite.
# When creating the composite, the tile downloaded from the first
# definition in the list (definition at index 0) is the base of the
# composite, whereas any subsequent layers are layers stacked on top
# of each other.
#
# The GET requests towards the proxy only support the slippy map format
# (https://wiki.openstreetmap.org/wiki/Slippy_map) where a zoom level,
# a x and a y tile index is requested. If the server you want to
# download tiles from doesn't speak slippy map format (x/y/z) but you
# know how to write a function to translate x/y/z to whatever the remote
# servers can understand, then you can create a configuration where
# where you define a python function called "dynGetTileUrl(z, x, y)"
# that is executed to find the url that will be used to download the
# given x/y/z tile and serve it to your application.
#
# Look at the 'norway_vfr' map definition below for an example of a
# map definition that uses the dynGenTileUrl, and the "openflightmaps"
# for an example of a map definition that uses a base layer and an
# overlay. Start the server, and use the following two links to see
# the tile proxy server in action:
#
# http://localhost:8080/openflightmaps/8/136/92
# http://localhost:8080/norway_vfr/11/1066/566

MAINCONFIG = {
    "norway_vfr": [
        {
            "dyn_tile_url": True,
            "tile_servers": ["""
def dynGetTileUrl(z, x, y):
    url = 'https://avigis.avinor.no/agsmap/rest/services/ICAO_500000/MapServer/export?'
    # User mercantile to find the bounding box
    bbox = mercantile.bounds(x, y, z)
    params = {
        "dpi": 96,
        "transparent": "true",
        "format": "png32",
        "layers": "show:3",
        "bbox": "{},{},{},{}".format(bbox.west, bbox.south, bbox.east, bbox.north),
        "bboxSR": 4326, # WGS 84: https://developers.arcgis.com/rest/services-reference/enterprise/export-image.htm
        "imageSR": 3857, # Web Mercator (3857): https://developers.arcgis.com/rest/services-reference/enterprise/export-image.htm
        "size": "128,128",
        "f": "image",
    }
    return url + urllib.parse.urlencode(params)
        """],
            "filetype": "png",
            "tile_width": 128,
            "tile_height": 128,
            "min_zoom": 2,
            "max_zoom": 15,
            "uses": [urllib, mercantile],
        }
    ],
    "openflightmaps": [
        {
            "tile_servers": [
                "nwy-tiles-api.prod.newaydata.com",
            ],
            "protocol": "https",
            "url": "tiles/{z}/{x}/{y}.jpg?path=latest/base/latest",
            "filetype": "jpg",
            "tile_width": 512,
            "tile_height": 512,
        },
        {
            "tile_servers": [
                "nwy-tiles-api.prod.newaydata.com",
            ],
            "protocol": "https",
            "url": "tiles/{z}/{x}/{y}.png?path=latest/aero/latest",
            "filetype": "png",
            "tile_width": 512,
            "tile_height": 512,
        },
    ],
    "openflighttopo": [
        {
            "tile_servers": [
                "a.tile.opentopomap.org",
                "b.tile.opentopomap.org",
                "c.tile.opentopomap.org",
            ],
            "protocol": "https",
            "url": "{z}/{x}/{y}.png",
            "filetype": "png",
            "tile_width": 256,
            "tile_height": 256,
        },
        {
            "tile_servers": [
                "nwy-tiles-api.prod.newaydata.com",
            ],
            "protocol": "https",
            "url": "tiles/{z}/{x}/{y}.png?path=latest/aero/latest",
            "filetype": "png",
            "tile_width": 512,
            "tile_height": 512,
        },
    ],
}

hostName = "localhost"
serverPort = 8080

# Two urls that work for openflightmaps and norway_vfr for testing
# http://localhost:8080/openflightmaps/8/136/92
# http://localhost:8080/norway_vfr/11/1066/566
MAX_DOWNLOAD_WORKERS = 16
URL_DOWNLOAD_TIMEOUT = 2  # seconds


def get_tile_url_from_conf(conf: Dict, z: int, x: int, y: int) -> str:
    # If "dyn_tile_url" is True, a function name "dynGetTileUrl" that
    # takes as input params z, x, and y must be provided in the
    # tile_servers definition. This function will then be executed, and
    # the return value is the url that will be used to download the x/y tile
    # for zoom z.
    if conf.get("dyn_tile_url") is True:
        exec(conf.get("tile_servers")[0], globals())
        return dynGetTileUrl(z, x, y)  # noqa

    server_idx = randint(0, len(conf.get("tile_servers")) - 1)
    return conf.get("protocol", "https") + "://" + conf.get("tile_servers")[
        server_idx] + "/" + conf["url"].format(z=z, x=x, y=y)


def get_tile_urls(incoming_url: str) -> List[str]:
    # Always expect a url in the form of /map_identifier/z/x/y
    data = incoming_url.rstrip('/').lstrip('/').split('/')
    if len(data) < 4:
        raise ValueError(
            "Error: expecting GET request in the form 'map_config/z/x/y'")

    map_conf = MAINCONFIG.get(data[0])
    if map_conf is None:
        raise IndexError(
            f"Error: no map '{data[0]}' found in the tile proxy conf")

    z = int(data[1])
    x = int(data[2])
    y = int(data[3])

    urls = []
    for conf in map_conf:
        urls.append(get_tile_url_from_conf(conf, z, x, y))
    return urls


def get_image_filetype(incoming_url: str) -> str:
    # When we only have one layer, return the layer filetype that is
    # defined in the conf.
    #
    # When having more than one layers, we make a composite with the
    # build_composite_image function that always returns a png
    data = incoming_url.rstrip('/').lstrip('/').split('/')
    if len(data) < 4:
        raise ValueError(
            "Error: expecting GET request in the form 'map_config/z/x/y'")

    map_conf = MAINCONFIG.get(data[0])
    if map_conf is None:
        raise IndexError(
            f"Error: no map '{data[0]}' found in the tile proxy conf")

    if len(map_conf) == 1:
        return map_conf[0]["filetype"]

    return "png"


def download_tile(url: str, timeout: int) -> bytes:
    # Downloads a tile from the url and returns the received bytes
    with urllib.request.urlopen(url, timeout=timeout) as conn:
        return conn.read()


def download_tiles(urls: List[str]) -> Dict[int, Dict[str, Union[str, bytes]]]:
    # Downloads tiles in parallel threads and returns the download
    # result in a dict where the key is an integer. The key indicates the
    # layering order when making image composites later on, whereas the
    # result with key 0 is the base image, and any subsequent image is
    # an overlay image
    images = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as executor:
        # Start the load operations and mark each future with its URL
        future_to_url = {
            executor.submit(
                download_tile, url, URL_DOWNLOAD_TIMEOUT): (url, url_idx)
            for url_idx, url in enumerate(urls)
        }

        for future in concurrent.futures.as_completed(future_to_url):
            url, url_idx = future_to_url[future]
            try:
                data = future.result()
            except Exception as exc:
                print('%r generated an exception: %s' % (url, exc))
                return {}
            else:
                print('Downloaded %r - %d bytes' % (url, len(data)))
                images[url_idx] = {"url": url, "data": data}
    return images


def build_composite_image(img_base: bytes, img_overlay: bytes) -> bytes:
    # Compose a base image and an overlay, and return the
    # generated PNG image as a blob
    base = Image(blob=img_base)
    overlay = Image(blob=img_overlay)

    b_w, b_h = base.size
    o_w, o_h = overlay.size

    # Resize base or overlay to the width/height of the smallest layer
    if b_w != o_w:
        if b_w > o_w:
            base.resize(o_w, o_h)
        else:
            overlay.resize(b_w, b_h)

    base.composite(overlay)
    return base.make_blob("PNG")


class HttpRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        print(" - Serving Incoming request " + self.path)
        try:
            urls = get_tile_urls(self.path)
            image_type = get_image_filetype(self.path)
            images = download_tiles(urls)
            if len(images) == 0:
                raise BaseException(
                    "failed to download all tiles for urls", urls)

            self.send_response(200)
            self.send_header("Content-type", f"image/{image_type}")
            self.end_headers()
            if len(images) == 1:
                self.wfile.write(images[0]["data"])
                return

            # Make a composite and then return
            composite = build_composite_image(
                images[0]["data"], images[1]["data"])
            self.wfile.write(composite)

        except BaseException as e:
            print("Error occured:", e)
            self.send_error(408)


if __name__ == "__main__":
    webServer = ThreadingHTTPServer(
        (hostName, serverPort), HttpRequestHandler)
    print("Tile proxy started http://%s:%s" % (hostName, serverPort))

    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        pass

    webServer.server_close()

    print("Server stopped.")
