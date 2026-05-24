# http3_server_min.py
import argparse, asyncio, importlib, logging, time
from email.utils import formatdate
from collections import deque
from typing import Callable, Dict, Optional, Deque

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import DataReceived, HeadersReceived, H3Event, DatagramReceived, WebTransportStreamDataReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ProtocolNegotiated, QuicEvent, DatagramFrameReceived

AsgiApplication = Callable
SERVER_NAME = "aioquic-min"

class HttpRequestHandler:
    def __init__(self, *, connection: H3Connection, scope: Dict, stream_id: int, transmit: Callable[[], None]) -> None:
        self.connection = connection
        self.scope = scope
        self.stream_id = stream_id
        self.transmit = transmit
        self._queue: asyncio.Queue[Dict] = asyncio.Queue()

    def http_event_received(self, event: H3Event) -> None:
        if isinstance(event, DataReceived):
            self._queue.put_nowait({
                "type": "http.request",
                "body": event.data,
                "more_body": not event.stream_ended,
            })
        elif isinstance(event, HeadersReceived) and event.stream_ended:
            self._queue.put_nowait({"type": "http.request", "body": b"", "more_body": False})

    async def run_asgi(self, app: AsgiApplication) -> None:
        await app(self.scope, self.receive, self.send, self.connection)

    async def receive(self) -> Dict:
        return await self._queue.get()

    async def send(self, message: Dict) -> None:
        if message["type"] == "http.response.start":
            headers = [
                (b":status", str(message["status"]).encode()),
                (b"server", SERVER_NAME.encode()),
                (b"date", formatdate(time.time(), usegmt=True).encode()),
            ] + [(k, v) for k, v in message["headers"]]
            self.connection.send_headers(stream_id=self.stream_id, headers=headers)

        elif message["type"] == "http.response.body":
            self.connection.send_data(
                stream_id=self.stream_id,
                data=message.get("body", b""),
                end_stream=not message.get("more_body", False),
            )

        self.transmit()

class WebTransportHandler:
    def __init__(
        self,
        *,
        connection: H3Connection,
        scope: dict,
        stream_id: int,
        transmit: Callable[[], None],
    ) -> None:
        self.accepted = False
        self.closed = False
        self.connection = connection
        self.http_event_queue: Deque[H3Event] = deque()
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.scope = scope
        self.stream_id = stream_id
        self.transmit = transmit

    def http_event_received(self, event: H3Event) -> None:
        if not self.closed:
            if self.accepted:
                if isinstance(event, DatagramReceived):
                    self.queue.put_nowait(
                        {
                            "data": event.data,
                            "type": "webtransport.datagram.receive",
                        }
                    )
                elif isinstance(event, WebTransportStreamDataReceived):
                    self.queue.put_nowait(
                        {
                            "data": event.data,
                            "stream": event.stream_id,
                            "type": "webtransport.stream.receive",
                        }
                    )
            else:
                # delay event processing until we get `webtransport.accept`
                # from the ASGI application
                self.http_event_queue.append(event)

    async def run_asgi(self, app: AsgiApplication) -> None:
        self.queue.put_nowait({"type": "webtransport.connect"})

        try:
            await app(self.scope, self.receive, self.send, self.connection)
        finally:
            if not self.closed:
                await self.send({"type": "webtransport.close"})

    async def receive(self) -> dict:
        return await self.queue.get()

    async def send(self, message: dict) -> None:
        data = b""
        end_stream = False

        if message["type"] == "webtransport.accept":
            self.accepted = True

            headers = [
                (b":status", b"200"),
                (b"server", SERVER_NAME.encode()),
                (b"date", formatdate(time.time(), usegmt=True).encode()),
                (b"sec-webtransport-http3-draft", b"draft02"),
            ]
            self.connection.send_headers(stream_id=self.stream_id, headers=headers)

            # consume backlog
            while self.http_event_queue:
                self.http_event_received(self.http_event_queue.popleft())
        elif message["type"] == "webtransport.close":
            if not self.accepted:
                self.connection.send_headers(
                    stream_id=self.stream_id, headers=[(b":status", b"403")]
                )
            end_stream = True
        elif message["type"] == "webtransport.datagram.send":
            if len(self.connection._quic._datagrams_pending) < 100:
                self.connection.send_datagram(
                    stream_id=self.stream_id, data=message["data"]
                )
            else:
                logging.warning("Dropping datagram because of congestion. Current pending datagrams: %d", len(self.connection._quic._datagrams_pending))
        elif message["type"] == "webtransport.stream.send":
            self.connection._quic.send_stream_data(
                stream_id=message["stream"], data=message["data"]
            )

        if data or end_stream:
            self.connection.send_data(
                stream_id=self.stream_id, data=data, end_stream=end_stream
            )
        if end_stream:
            self.closed = True
        self.transmit()

class Http3Protocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._http: Optional[H3Connection] = None
        self._handlers: Dict = {}

    def http_event_received(self, event: H3Event) -> None:
        if isinstance(event, HeadersReceived) and event.stream_id not in self._handlers:
            # Build ASGI scope
            headers = []
            method = "GET"
            raw_path = b"/"
            protocol = None
            for k, v in event.headers:
                if k == b":method":
                    method = v.decode()
                elif k == b":path":
                    raw_path = v
                elif k == b":protocol":
                    protocol = v.decode()
                elif k and not k.startswith(b":"):
                    headers.append((k, v))

            if b"?" in raw_path:
                path_bytes, query = raw_path.split(b"?", 1)
            else:
                path_bytes, query = raw_path, b""

            # Peer address
            client_addr = self._http._quic._network_paths[0].addr  # type: ignore
            client = (client_addr[0], client_addr[1])

            handler = None
            if method == "CONNECT" and protocol == "webtransport":
                assert isinstance(self._http, H3Connection), (
                    "WebTransport is only supported over HTTP/3"
                )
                scope = {
                    "client": client,
                    "headers": headers,
                    "http_version": "3",
                    "method": method,
                    "path": path_bytes.decode(),
                    "query_string": query,
                    "raw_path": raw_path,
                    "root_path": "",
                    "scheme": "https",
                    "type": "webtransport",
                }
                handler = WebTransportHandler(
                    connection=self._http,
                    scope=scope,
                    stream_id=event.stream_id,
                    transmit=self.transmit,
                )

            else: 
                scope = {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "3",
                    "method": method,
                    "scheme": "https",
                    "path": path_bytes.decode(),
                    "raw_path": raw_path,
                    "query_string": query,
                    "headers": headers,
                    "client": client,
                    "server": None,
                }

                handler = HttpRequestHandler(
                    connection=self._http, scope=scope, stream_id=event.stream_id, transmit=self.transmit
                )
            self._handlers[event.stream_id] = handler
            asyncio.create_task(handler.run_asgi(application))
        elif isinstance(event, (HeadersReceived, DataReceived)) and event.stream_id in self._handlers:
            self._handlers[event.stream_id].http_event_received(event)
        elif isinstance(event, WebTransportStreamDataReceived):
            self._handlers[event.session_id].http_event_received(event)
        elif isinstance(event, DatagramReceived):
            self._handlers[event.stream_id].http_event_received(event)

    def quic_event_received(self, event: QuicEvent) -> None:        
        if isinstance(event, ProtocolNegotiated):
            if event.alpn_protocol in H3_ALPN:
                self._http = H3Connection(self._quic, enable_webtransport=True)
        elif isinstance(event, DatagramFrameReceived):
            pass # TODO: see what this can do

        if self._http is not None:
            for http_event in self._http.handle_event(event):
                self.http_event_received(http_event)

async def main(host: str, port: int, configuration: QuicConfiguration) -> None:
    await serve(host, port, configuration=configuration, create_protocol=Http3Protocol)
    await asyncio.Future()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Minimal HTTP/3 QUIC server for ASGI apps")
    parser.add_argument("app", type=str, nargs="?", default="gs_app:app",
                        help="ASGI application as <module>:<attribute>")
    parser.add_argument("-c", "--certificate", type=str, required=True)
    parser.add_argument("-k", "--private-key", type=str, required=True)
    parser.add_argument("--host", type=str, default="::")
    parser.add_argument("--port", type=int, default=4433)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    module_str, attr_str = args.app.split(":", 1)
    module = importlib.import_module(module_str)
    application = getattr(module, attr_str)

    config = QuicConfiguration(
        alpn_protocols=H3_ALPN + ["siduck"],
        is_client=False,
        max_datagram_frame_size=65536,
        max_datagram_size=1400,  # safe default for many paths
    )
    config.load_cert_chain(args.certificate, args.private_key)

    try:
        asyncio.run(main(args.host, args.port, config))
    except KeyboardInterrupt:
        pass
