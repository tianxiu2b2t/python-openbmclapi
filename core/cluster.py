import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import io
import os
from pathlib import Path
import time
import aiofiles
import aiohttp
from typing import Any
import socketio
from config import Config
from core.timer import Timer  # type: ignore
import pyzstd as zstd
from avro import schema, io as avro_io
import core.utils as utils
import core.stats as stats
import core.web as web
from core.logger import logger
from tqdm import tqdm

version = "1.9.7"
api_version = "1.9.7"
user_agent = f"openbmclapi-cluster/{api_version} python-openbmclapi/{version}"
base_url = "https://openbmclapi.bangbang93.com/"
cluster_id = Config.get("cluster_id")
cluster_secret = Config.get("cluster_secret")
io_buffer = Config.get("io_buffer")
max_download = Config.get("max_download")
byoc = Config.get("byoc")
public_host = Config.get("public_host")
public_port = Config.get("public_port")
port = Config.get("port")


@dataclass
class BMCLAPIFile:
    path: str
    hash: str
    size: int


class TokenManager:
    def __init__(self) -> None:
        self.token = None

    async def fetchToken(self):
        async with aiohttp.ClientSession(
            headers={"User-Agent": user_agent}, base_url=base_url
        ) as session:
            try:
                async with session.get(
                    "/openbmclapi-agent/challenge", params={"clusterId": cluster_id}
                ) as req:
                    req.raise_for_status()
                    challenge: str = (await req.json())["challenge"]

                signature = hmac.new(
                    cluster_secret.encode("utf-8"), digestmod=hashlib.sha256
                )
                signature.update(challenge.encode())
                signature = signature.hexdigest()

                data = {
                    "clusterId": cluster_id,
                    "challenge": challenge,
                    "signature": signature,
                }

                async with session.post("/openbmclapi-agent/token", json=data) as req:
                    req.raise_for_status()
                    content: dict[str, Any] = await req.json()
                    self.token = content["token"]
                    Timer.delay(
                        self.fetchToken, delay=float(content["ttl"]) / 1000.0 - 600
                    )

            except aiohttp.ClientError as e:
                logger.error(f"Error fetching token: {e}.")

    async def getToken(self) -> str:
        if not self.token:
            await self.fetchToken()
        return self.token or ""


class Progress:
    def __init__(self, data, func) -> None:
        self.func = func
        self.data = data
        self.total = len(data)
        self.cur = 0
        self.cur_speed = 0
        self.cur_time = time.time()

    def process(self):
        for data in self.data:
            self.func(data)
            self.cur += 1
            self.cur_speed += 1
            yield self.cur, self.total
            if time.time() - self.cur_time >= 1:
                self.cur_speed = 0


class FileStorage:
    def __init__(self, dir: Path) -> None:
        self.dir = dir
        if self.dir.is_file():
            raise FileExistsError("The path is file.")
        self.dir.mkdir(exist_ok=True, parents=True)
        self.files: asyncio.Queue[BMCLAPIFile] = asyncio.Queue()
        self.download_bytes = utils.Progress(5)
        self.download_files = utils.Progress(5)
        self.sio = socketio.AsyncClient()
        self.keepalive = None
        self.timeout = None
        self.last_hit = 0
        self.last_bytes = 0
        self.last_cur = 0

    async def download(self, session: aiohttp.ClientSession):
        while not self.files.empty():
            file = await self.files.get()
            hash = utils.get_hash(file.hash)
            size = 0
            filepath = Path(str(self.dir) + "/" + file.hash[:2] + "/" + file.hash)
            try:
                async with session.get(file.path) as resp:
                    filepath.parent.mkdir(exist_ok=True, parents=True)
                    async with aiofiles.open(filepath, "wb") as w:
                        while data := await resp.content.read(io_buffer):
                            if not data:
                                break
                            byte = len(data)
                            size += byte
                            self.download_bytes.add(byte)
                            await w.write(data)
                            hash.update(data)
                if file.hash != hash.hexdigest():
                    filepath.unlink(True)
                    raise EOFError
                self.download_files.add()
            except:
                self.download_bytes.add(-size)
                await self.files.put(file)

    async def check_file(self):
        logger.info("Requesting filelist...")
        filelist = await self.get_file_list()
        filesize = sum((file.size for file in filelist))
        total = len(filelist)
        byte = 0
        miss = []
        pbar = tqdm(total=total, unit=" file(s)", unit_scale=True)
        pbar.set_description("Checking files")
        for i, file in enumerate(filelist):
            filepath = str(self.dir) + f"/{file.hash[:2]}/{file.hash}"
            if not os.path.exists(filepath) or os.path.getsize(filepath) != file.size:
                miss.append(file)
            await asyncio.sleep(0)
            byte += file.size
            pbar.update(1)
        if not miss:
            logger.info("Checked all files!")
            await self.start_service()
            return
        filelist = miss
        filesize = sum((file.size for file in filelist))
        total = len(filelist)
        logger.info(f"Missing files: {total}({utils.calc_bytes(filesize)}).")
        for file in filelist:
            await self.files.put(file)
        self.download_bytes = utils.Progress(5, filesize)
        self.download_files = utils.Progress(5)
        timers = []
        for _ in range(0, max_download, 32):
            for __ in range(32):
                timers.append(
                    Timer.delay(
                        self.download,
                        args=(
                            aiohttp.ClientSession(
                                base_url,
                                headers={
                                    "User-Agent": user_agent,
                                    "Authorization": f"Bearer {await token.getToken()}",
                                },
                            ),
                        ),
                    )
                )
        pbar = tqdm(total=total, unit=" file(s)", unit_scale=True)
        pre = 0
        while any([not timer.called for timer in timers]):
            bits = self.download_bytes.get_cur_speeds() or [0]
            minbit = min(bits)
            bit = utils.calc_more_bit(minbit, bits[-1], max(bits))
            pbar.set_description(f"Downloading files | Curent speed: {bit[2]}")
            await asyncio.sleep(1)
            pbar.update(self.download_files.get_cur() - pre)
            pre = self.download_files.get_cur()
        await self.start_service()

    async def start_service(self):
        tokens = await token.getToken()
        await self.sio.connect(
            base_url,
            transports=["websocket"],
            auth={"token": tokens},
        )  # type: ignore
        await self.enable()

    async def enable(self):
        if not self.sio.connected:
            return
        await self.emit(
            "enable",
            {
                "host": public_host,
                "port": public_port or port,
                "version": version,
                "byoc": byoc,
                "noFastEnable": False,
            },
        )
        if not web.get_ssl() and not (
            Path(".ssl/cert.pem").exists() and Path(".ssl/key.pem").exists()
        ):
            await self.emit("request-cert")
        logger.info("Connected to the Main Server.")
        self.keepalive = Timer.delay(self.keepaliveTimer, (), 5)

    async def message(self, type, data):
        if type == "request-cert":
            cert = data[1]
            logger.info("Requested cert!")
            cert_file = Path(".ssl/cert.pem")
            key_file = Path(".ssl/key.pem")
            for file in (cert_file, key_file):
                file.parent.mkdir(exist_ok=True, parents=True)
            with open(cert_file, "w") as w:
                w.write(cert["cert"])
            with open(key_file, "w") as w:
                w.write(cert["key"])
            web.load_cert()
            cert_file.unlink()
            key_file.unlink()
        elif type == "enable":
            if self.keepalive:
                self.keepalive.block()
            self.keepalive = Timer.delay(self.keepaliveTimer, (), 5)
            if data[0]:
                logger.error(data[0]["message"])
                return
            if data[1] == True:
                logger.info("Checked! Starting the service")
                return
            logger.error(data[0]["message"])
            Timer.delay(self.start_service, (), 5)
        elif type == "keep-alive":
            if self.keepalive:
                self.keepalive.block()
            if self.timeout:
                self.timeout.block()
            if data[0]:
                logger.error(data[0]["message"])
                return
            hit = self.last_hit - stats.get_counter(self.last_cur).sync_hit
            byte = utils.calc_bytes(
                self.last_bytes - stats.get_counter(self.last_cur).sync_bytes
            )
            logger.info(f"Keepalive serve: {hit}file{'s' if hit != 1 else ''}({byte})")
            stats.get_counter(self.last_cur).sync_hit = self.last_hit
            stats.get_counter(self.last_cur).sync_bytes = self.last_bytes
            self.keepalive = Timer.delay(self.keepaliveTimer, (), 5)

    async def keepaliveTimer(self):
        counter = stats.get_counter()
        self.last_hit = counter.hit
        self.last_bytes = counter.bytes
        self.last_cur = stats.get_hour(0)
        await self.emit(
            "keep-alive",
            {
                "time": time.time(),
                "hits": counter.hit - counter.sync_hit,
                "bytes": counter.bytes - counter.sync_bytes,
            },
        )
        self.timeout = Timer.delay(self.timeoutTimer, (), 30)

    async def timeoutTimer(self):
        Timer.delay(self.start_service, 0)

    async def emit(self, channel, data=None):
        await self.sio.emit(
            channel, data, callback=lambda x: Timer.delay(self.message, (channel, x))
        )

    async def get_file_list(self):
        async with aiohttp.ClientSession(
            headers={
                "User-Agent": user_agent,
                "Authorization": f"Bearer {await token.getToken()}",
            },
            base_url=base_url,
        ) as session:
            async with session.get(
                "/openbmclapi/files", data={"responseType": "buffer", "cache": ""}
            ) as req:
                req.raise_for_status()
                logger.info("Requested filelist.")

                parser = avro_io.DatumReader(
                    schema.parse(
                        """  
{  
  "type": "array",  
  "items": {  
    "type": "record",  
    "name": "FileList",  
    "fields": [  
      {"name": "path", "type": "string"},  
      {"name": "hash", "type": "string"},  
      {"name": "size", "type": "long"}  
    ]  
  }  
}  
"""
                    )
                )
                decoder = avro_io.BinaryDecoder(
                    io.BytesIO(zstd.decompress(await req.read()))
                )
                return [BMCLAPIFile(**file) for file in parser.read(decoder)]


class FileCache:
    def __init__(self, file: Path) -> None:
        self.buf = io.BytesIO()
        self.size = 0
        self.last_file = 0
        self.last = 0
        self.file = file
        self.access = 0

    async def __call__(self) -> io.BytesIO:
        self.access = time.time()
        if self.last < time.time():
            stat = self.file.stat()
            if self.size == stat.st_size and self.last_file == stat.st_mtime:
                self.last = time.time() + 1440
                return self.buf
            self.buf = io.BytesIO()
            async with aiofiles.open(self.file, "rb") as r:
                while (
                    data := await r.read(min(io_buffer, stat.st_size - self.buf.tell()))
                ) and self.buf.tell() < stat.st_size:
                    self.buf.write(data)
                self.last = time.time() + 1440
                self.size = stat.st_size
                self.last_file = stat.st_mtime
            self.buf.seek(0, os.SEEK_SET)
        return self.buf


cache: dict[str, FileCache] = {}
token = TokenManager()
storage: FileStorage = FileStorage(Path("bmclapi"))


async def init():
    global storage
    Timer.delay(storage.check_file)
    app = web.app

    @app.get("/measure/{size}")
    async def _(request: web.Request, size: int, s: str, e: str):
        # if not config.SKIP_SIGN:
        #    check_sign(request.protocol + "://" + request.host + request.path, config.CLUSTER_SECRET, s, e)
        async def iter(size):
            for _ in range(size):
                yield b"\x00" * 1024 * 1024

        return web.Response(iter(size))

    @app.get("/download/{hash}")
    async def _(request: web.Request, hash: str, s: str, e: str):
        # if not config.SKIP_SIGN:
        #    check_sign(request.protocol + "://" + request.host + request.path, config.CLUSTER_SECRET, s, e)
        file = Path(str(storage.dir) + "/" + hash[:2] + "/" + hash)
        stats.get_counter().qps += 1
        if not file.exists():
            return web.Response(status_code=404)
        if hash not in cache:
            cache[hash] = FileCache(file)
        data = await cache[hash]()
        stats.get_counter().bytes += cache[hash].size
        stats.get_counter().hit += 1
        return data.getbuffer()

    router: web.Router = web.Router("/bmcl")
    dir = Path("./bmclapi_dashboard/")
    dir.mkdir(exist_ok=True, parents=True)
    app.mount_resource(web.Resource("/bmcl", dir, show_dir=True))

    @router.get("/")
    async def _(request: web.Request):
        return Path("./bmclapi_dashboard/index.html")

    @router.get("/master")
    async def _(request: web.Request, url: str):
        content = io.BytesIO()
        async with aiohttp.ClientSession(base_url) as session:
            async with session.get(url) as resp:
                content.write(await resp.read())
        return content  # type: ignore

    @router.get("/dashboard")
    async def _():
        return {"hourly": stats.hourly(), "days": stats.days()}

    app.mount(router)


async def clearCache():
    global cache
    data = cache.copy()
    size = 0
    for k, v in data.items():
        if v.access + 1440 < time.time():
            cache.pop(k)
        else:
            size += v.size
    if size > 1024 * 1024 * 512:
        data = cache.copy()
        for k, v in data.items():
            if size > 1024 * 1024 * 512:
                cache.pop(k)
                size -= v.size
            else:
                break


Timer.repeat(clearCache, (), 5, 10)
