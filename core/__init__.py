from dataclasses import dataclass
from enum import Enum
import os
import traceback
from .config import Config
from .timer import Timer
from .utils import Client
from .certificate import *
from . import web
from .logger import logger

import asyncio
import ssl
from typing import Optional


class Protocol(Enum):
    HTTP = "HTTP"
    Unknown = "Unknown"
    DETECT = "Detect"
    @staticmethod
    def get(data: bytes):
        if b'HTTP/1.1' in data:
            return Protocol.HTTP
        if check_port_key == data:
            return Protocol.DETECT
        return Protocol.Unknown
@dataclass
class ProxyClient:
    proxy: 'Proxy'
    origin: Client
    target: Client
    before: bytes = b''
    closed: bool = False
    def start(self):
        self._task_origin = Timer.delay(self.process_origin, (), 0)
        self._task_target = Timer.delay(self.process_target, (), 0) 
    async def process_origin(self):
        try:
            self.target.write(self.before)
            while (buffer := await self.origin.read(IO_BUFFER, timeout=TIMEOUT)) and not self.origin.is_closed() and not self.origin.is_closed():
                self.target.write(buffer)
                self.before = b''
                await self.target.writer.drain()
        except:
            ...
        self.close()
    async def process_target(self):
        try:
            while (buffer := await self.target.read(IO_BUFFER, timeout=TIMEOUT)) and not self.target.is_closed() and not self.target.is_closed():
                self.origin.write(buffer)
                await self.origin.writer.drain()
        except:
            ...
        self.close()
    def close(self):
        if not self.closed:
            if not self.origin.is_closed():
                self.origin.close()
            if not self.target.is_closed():
                self.target.close()
            self.closed = True
        self.proxy.disconnect(self)
class Proxy:
    def __init__(self) -> None:
        self._tables: list[ProxyClient] = []
    async def connect(self, origin: Client, target: Client, before: bytes):
        client = ProxyClient(self, origin, target, before)
        self._tables.append(client)
        client.start()
    def disconnect(self, client: ProxyClient):
        if client not in self._tables:
            return
        self._tables.remove(client)
    def get_origin_from_ip(self, ip: tuple[str, int]):
        # ip is connected client
        for target in self._tables:
            if target.target.get_sock_address() == ip:
                return target.origin.get_address()
        return None

ssl_server: Optional[asyncio.Server] = None
server: Optional[asyncio.Server] = None
proxy: Proxy = Proxy()
restart = False
check_port_key = os.urandom(8)
PORT: int = Config.get_integer("web.port") 
TIMEOUT: int = Config.get_integer("advanced.timeout") 
SSL_PORT: int = Config.get_integer("web.ssl_port")
PROTOCOL_HEADER_BYTES = Config.get_integer("advanced.header_bytes", 4096)
IO_BUFFER: int = Config.get_integer("advanced.io_buffer")
DEBUG: bool = Config.get_boolean("advanced.debug")

async def _handle_ssl(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    return await _handle_process(Client(reader, writer, peername = proxy.get_origin_from_ip(writer.get_extra_info("peername"))), True)

async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    return await _handle_process(Client(reader, writer))

async def _handle_process(client: Client, ssl: bool = False):
    global ssl_server
    proxying = False
    try:
        while (header := await client.read(PROTOCOL_HEADER_BYTES, timeout=30)) and not client.is_closed():
            protocol = Protocol.get(header)
            if protocol == Protocol.DETECT:
                client.write(check_port_key)
                await client.writer.drain()
                break
            if protocol == Protocol.Unknown and not ssl and ssl_server:
                target = Client(*(await asyncio.open_connection("127.0.0.1", ssl_server.sockets[0].getsockname()[1])), peername=client.get_address())
                proxying = True
                await proxy.connect(client, target, header)
                break
            elif protocol == Protocol.HTTP:
                await web.handle(header, client)
    except (
        TimeoutError,
        asyncio.exceptions.IncompleteReadError,
        ConnectionResetError,
    ):
        ...
    except:
        logger.debug(traceback.format_exc())
    if not proxying and not client.is_closed():
        client.close()
async def check_ports():
    global ssl_server, server, client_side_ssl, restart, check_port_key
    while 1:
        ports: list[tuple[asyncio.Server, ssl.SSLContext | None]] = []
        for service in ((server, None), (ssl_server, client_side_ssl if get_loads() != 0 else None)):
            if not service[0]:
                continue
            ports.append((service[0], service[1]))
        closed = False
        for port in ports:
            try:
                client = Client(*(await asyncio.open_connection('127.0.0.1', port[0].sockets[0].getsockname()[1], ssl=port[1])))
                client.write(check_port_key)
                await client.writer.drain()
                key = await client.read(len(check_port_key), 5)
            except:
                logger.warn(f"Port {port[0].sockets[0].getsockname()[1]} is shutdown now! Now restarting the port!")
                logger.error(traceback.format_exc())
                closed = True
        if closed:
            restart = True
            for port in ports:
                port[0].close()
        await asyncio.sleep(5)
async def main():
    global ssl_server, server, server_side_ssl, restart
    await web.init()
    certificate.load_cert(Path(".ssl/cert"), Path(".ssl/key"))
    Timer.delay(check_ports, (), 5)
    while 1:
        try:
            server = await asyncio.start_server(_handle, port=PORT)
            ssl_server = await asyncio.start_server(_handle_ssl, port=0 if SSL_PORT == PORT else SSL_PORT, ssl=server_side_ssl if get_loads() != 0 else None)
            logger.info(f"Listening server on {PORT}")
            logger.info(f"Listening server on {ssl_server.sockets[0].getsockname()[1]} Loaded certificates: {get_loads()}")
            async with server, ssl_server:
                await asyncio.gather(server.serve_forever(), ssl_server.serve_forever())
        except asyncio.CancelledError:
            if restart:
                if server:
                    server.close()
                restart = False
            else:
                logger.info("Shutdown web service")
                await web.close()
                break
        except:
            if server:
                server.close()
            logger.error(traceback.format_exc())
            await asyncio.sleep(2)


def init():
    asyncio.run(main())