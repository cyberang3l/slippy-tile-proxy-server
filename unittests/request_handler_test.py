import importlib
import unittest
from io import BytesIO as IO

root = importlib.import_module("slippy-tile-proxy-server")


class MockSocket(object):
    def getsockname(self):
        return ('sockname',)


class MockRequest(object):
    _sock = MockSocket()

    def __init__(self, test_caller: unittest.TestCase, path: str, expect_in_response: str):
        self._test = test_caller
        self._path = path
        self._expect = expect_in_response

    def sendall(self, response):
        self._test.assertTrue(response.decode().find(self._expect))

    def makefile(self, *args, **kwargs):
        if args[0] == 'rb':
            return IO(f"GET {self._path} HTTP/1.1".encode())
        elif args[0] == 'wb':
            return IO(b'')
        else:
            raise ValueError("Unknown file type to make", args, kwargs)


class TestHttpRequestHandler(unittest.TestCase):
    def setUp(self):
        pass

    def _test(self, request):
        return root.HttpRequestHandler(request, (0, 0), None)

    def test_parse_url(self):
        self._test(MockRequest(test_caller=self, path="/", expect_in_response="Error code: 408"))
