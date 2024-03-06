# Slippy map tile proxy server
Script to serve composite tiles and tiles from non-slippy-map servers, to programs that can only speak to slippy map servers

The tile proxy server solves me two issues I often experience
with different programs that only support slippy map tile
downloading from single sources:

1. Many nice online maps often use layers to display extra
information, and programs that only support downloading single x/y/z
tiles from a single server, cannot make use of the merged result
(base layer with overlays). The tile proxy server can download multiple
layers, make composite images on the fly, by downloading the base
layer and any configured overlay, and serve back the merged result
to programs that can't do that on their own.

2. Unfortunately, many servers often use different protocols
(TMS, WMTS, WMS, etc) that are more complicated for me (and I guess
for others too since many program don't support interaction with
such servers) to understand. Regardless, I still want to interact with
such servers from programs that only support the slippy map format.
The tile proxy server offers a solution to this problem with the
dynamic tile url generation functionality: your application still
requests x/y/z tiles, and you can write a function in your map
configuration to translate x/y/z to what a TMS/WMTS/WMS server can
understand, fetch the tile, and serve it back to the program that only
has slippy tile map support.

As I mentioned above, I don't understand much about TMS/WMTS servers,
but the python mercantile and pyproj packages can make such conversions
look simple.

See the sample `norway_vfr` map in the `mainConf` configuration, to
see how you can use this feature. In the `mainConf` you can also
look at the sample `openflightmaps` configuration that uses a base and
an overlay to serve a merged composite tile.

# Usage

1. Edit the `mainConf` dictionary in the `slippy-tile-proxy-server.py`
   file and add the map configuration that you would like the tile proxy
   server to serve (read the comments and have a look at the sample
   configurations that are included to understand how to add your
   configuration).
2. Save the script.
3. Run the script in a console: `python3 slippy-tile-proxy-server.py`
4. Make HTTP GET requests from your browser, or point any other program
   from your local computer to the url http://localhost:8080/map_identifier/z/x/y
