import asyncio
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import hashlib
import inspect
import io
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable, Iterable, Optional, Type, Union, get_args
import aiofiles
from rich.console import Console
from rich.text import Text
import traceback as traceback_
import Globals

class Task:
    def __init__(self, target, args, loop: bool = False, delay: float = 0, interval: float = 0, back = None) -> None:
        self.target = target
        self.args = args
        self.loop = loop
        self.delay = delay
        self.interval = interval
        self.last = 0.0
        self.create_at = time.time()
        self.blocked = False
        self.back = back
    async def call(self):
        if self.blocked:
            return
        try:
            if inspect.iscoroutinefunction(self.target):
                await self.target(*self.args)
            else:
                self.target(*self.args)
            await self.callback()
        except:
            traceback()
    async def callback(self):
        if not self.back:
            return
        try:
            if inspect.iscoroutinefunction(self.target):
                await self.back()
            else:
                self.back()
        except:
            traceback()
    def block(self):
        self.blocked = True
class TimerManager:
    def delay(self, target, args = (), delay: float = 0, callback = None):
        task = Task(target=target, args=args, delay=delay, back=callback)
        asyncio.get_event_loop().call_later(task.delay, lambda: asyncio.run_coroutine_threadsafe(task.call(), asyncio.get_event_loop()))
        return task
    def repeat(self, target, args = (), delay: float = 0, interval: float = 0, callback = None):
        task = Task(target=target, args=args, delay=delay, loop=True, interval=interval, back=callback)
        asyncio.get_event_loop().call_later(task.delay, lambda: self._repeat(task))
        return task
    def _repeat(self, task: Task):
        asyncio.get_event_loop().call_later(0, lambda: asyncio.run_coroutine_threadsafe(task.call(), asyncio.get_event_loop()))
        asyncio.get_event_loop().call_later(task.interval, lambda: self._repeat(task))
Timer: TimerManager = TimerManager()

@dataclass
class BMCLAPIFile:
    size: int = 0
    hash: str = ""
    path: str = ""

def updateDict(org: dict, new: dict):
    n = org.copy()
    n.update(new)
    return n

@dataclass
class Client:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    keepalive_connection: bool = False
    server_port: int = 0
    bytes_recv: int = 0
    bytes_sent: int = 0
    read_data: int = 0
    read_time: Optional[float] = None
    unchecked: bool = True
    log_network: Optional[Callable] = None
    compressed: bool = False
    is_ssl: bool = False
    def get_server_port(self):
        return self.server_port
    def _record_after(self, start_time: float, data) -> bytes:
        if self.unchecked:
            return data
        if not self.read_time:
            self.read_time = time.time()
        end_time = time.time() - start_time
        self.read_data += len(data)
        self.read_time += end_time
        speed = self.read_data / max(1, end_time)
        if speed < Globals.MIN_RATE and self.read_time > Globals.MIN_RATE_TIMESTAMP:
            raise TimeoutError("Data read speed is too low")
        return data
    async def readline(self, timeout: Optional[float] = Globals.TIMEOUT):
        start_time = time.time()
        data = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
        self.record_network(0, len(data))
        return self._record_after(start_time, data)

    async def readuntil(self, separator: bytes | bytearray | memoryview = b"\n", timeout: Optional[float] = Globals.TIMEOUT):
        start_time = time.time()
        data = await asyncio.wait_for(self.reader.readuntil(separator=separator), timeout=timeout)
        self.record_network(0, len(data))
        return self._record_after(start_time, data)

    async def read(self, n: int = -1, timeout: Optional[float] = Globals.TIMEOUT):
        start_time = time.time()
        data: bytes = await asyncio.wait_for(self.reader.read(n), timeout=timeout)
        self.record_network(0, len(data))
        return self._record_after(start_time, data)

    async def readexactly(self, n: int, timeout: Optional[float] = Globals.TIMEOUT):
        start_time = time.time()
        data = await asyncio.wait_for(self.reader.readexactly(n), timeout=timeout)
        self.record_network(0, len(data))
        return self._record_after(start_time, data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        val = await self.readline()
        if val == b'':
            raise StopAsyncIteration
        return val

    def get_address(self):
        return self.writer.get_extra_info("peername")[:2]

    def get_ip(self):
        return self.get_address()[0]
    def get_port(self):
        return self.get_address()[1]

    def write(self, data: bytes | bytearray | memoryview):
        if self.is_closed():
            return -1
        try:
            self.writer.write(data)
            length: int = len(data)
            self.record_network(length, 0)
            return length
        except:
            self.close()
        return -1

    def writelines(self, data: Iterable[bytes | bytearray | memoryview]):
        if self.is_closed():
            return -1
        try:
            self.writer.writelines(data)
            length: int = sum([len(raw_data) for raw_data in data])
            self.record_network(length, 0)
            return length
        except:
            self.close()
        return -1

    def set_keepalive_connection(self, value: bool):
        self.keepalive_connection = value

    def close(self):
        return self.writer.close()
    def is_closed(self):
        return self.writer.is_closing()
    def set_log_network(self, handler):
        self.log_network = handler
    def record_network(self, sent: int, recv: int):
        if not self.log_network:
            return
        self.log_network(self, sent, recv)


def get_hash(org):
    if len(org) == 32: return hashlib.md5()
    else: return hashlib.sha1()

async def get_file_hash(org: str, path: Path):
    hash = get_hash(org)
    async with aiofiles.open(path, "rb") as r:
        while data := await r.read(Globals.BUFFER):
            if not data:
                break
            hash.update(data)
            await asyncio.sleep(0.001)
    return hash.hexdigest() == org

byte_unit: tuple = ("", "K", "M", "G", "T", "E")

def calc_bytes(byte):
    cur = 0
    while byte // 1024.0 >= 1 and cur < len(byte_unit) - 1:
        cur += 1
        byte /= 1024.0
    return float(Decimal(byte).quantize(Decimal("0.01"), rounding = "ROUND_HALF_UP")), byte_unit[cur]

def calc_more_bytes(*bytes):
    cur = 0
    byte = max(bytes)
    while byte // 1024.0 >= 1 and cur < len(byte_unit) - 1:
        cur += 1
        byte /= 1024.0
    return [float(Decimal(b / (1024.0 ** cur)).quantize(Decimal("0.01"), rounding = "ROUND_HALF_UP")) for b in bytes], byte_unit[cur]

threads: list[threading.Thread] = []
def append_thread(thread: threading.Thread):
    global threads
    threads.append(thread)
    return thread

class ChatColor(Enum):
    BLACK = {
        "code": "0",
        "name": "black",
        "color": 0
    }
    DARK_BLUE = {
        "code": "1",
        "name": "dark_blue",
        "color": 170
    }
    DARK_GREEN = {
        "code": "2",
        "name": "dark_green",
        "color": 43520
    }
    DARK_AQUA = {
        "code": "3",
        "name": "dark_aqua",
        "color": 43690
    }
    DARK_RED = {
        "code": "4",
        "name": "dark_red",
        "color": 11141120
    }
    DARK_PURPLE = {
        "code": "5",
        "name": "dark_purple",
        "color": 11141290
    }
    GOLD = {
        "code": "6",
        "name": "gold",
        "color": 16755200
    }
    GRAY = {
        "code": "7",
        "name": "gray",
        "color": 11184810
    }
    DARK_GRAY = {
        "code": "8",
        "name": "dark_gray",
        "color": 5592405
    }
    BLUE = {
        "code": "9",
        "name": "blue",
        "color": 5592575
    }
    GREEN = {
        "code": "a",
        "name": "green",
        "color": 5635925
    }
    AQUA = {
        "code": "b",
        "name": "aqua",
        "color": 5636095
    }
    RED = {
        "code": "c",
        "name": "red",
        "color": 16733525
    }
    LIGHT_PURPLE = {
        "code": "d",
        "name": "light_purple",
        "color": 16733695
    }
    YELLOW = {
        "code": "e",
        "name": "gray",
        "color": 16777045
    }
    WHITE = {
        "code": "f",
        "name": "white",
        "color": 16777215
    }
    @staticmethod
    def getAllowcateCodes():
        code: str = ""
        for value in list(ChatColor):
            code += value.value["code"]
        return code
    @staticmethod
    def getByChatToHex(code: str):
        if len(code) != 1: return "000000"
        for value in list(ChatColor):
            if code == value.value["code"]:
                return f"{value.value['color']:06X}"
        return "000000"
    @staticmethod
    def getByChatToName(code: str):
        if len(code) != 1: return "black"
        for value in list(ChatColor):
            if code == value.value["code"]:
                return value.value["name"]
        return "black"

console = Console(color_system='auto')
Force = True
log_dir = Path("web_logs")
log_dir.mkdir(exist_ok=True)
is_debug = True
logs: queue.Queue = queue.Queue()

def logger(*message, level: int = 0, force = False):
    global Force, logs
    if Force or force:
        datetime: time.struct_time = time.localtime()
        msg: Text = formatColor(f"§{(getLevelColor(level)).value['code']}[{datetime.tm_year:04d}-{datetime.tm_mon:02d}-{datetime.tm_mday:02d} {datetime.tm_hour:02d}:{datetime.tm_min:02d}:{datetime.tm_sec:02d}] [{getLevel(level)}] " + ' '.join([str(msg) for msg in message]))
        console.print(msg)
        logs.put(msg)

def check_log():
    global logs, log_dir
    with open(str(log_dir) + "/logs.log", "a") as w:
        while 1:
            while not logs.empty():
                w.write(str(logs.get()) + "\n")
                w.flush()

threading.Thread(target=check_log,).start()

def warn(*message):
    return logger(*message, level = 1)

def info(*message):
    return logger(*message, level = 0)

def error(*message):
    return logger(*message, level = 2, force=True)

def traceback(force: bool = True):
    global is_debug
    if is_debug or force:
        return error(traceback_.format_exc())

def debug(*message):
    return logger(*message, level = 3)

def formatColor(message: str) -> Text:
    text: Text = Text("")
    temp: str
    start: int = 0
    while (start := message.find("§", start)) != -1:
        if (start + 1) > len(message): break
        if message.find("§", start + 1) != -1:
            temp = message[start + 2 : message.index("§", start + 1)]
        else:
            temp = message[start + 2:]
        text.append(temp, style=f"{ChatColor.getByChatToName(message[start + 1 : start + 2])}")
        start += 1
    return text

def getLevel(level: int = 0):
    match (level):
        case 0:
            return "INFO"
        case 1:
            return "WARN"
        case 2:
            return "ERROR"
        case 3:
            return "DEBUG"
        case _:
            return "LOGGER"

def getLevelColor(level: int = 0):
    match (level):
        case 0:
            return ChatColor.GREEN
        case 1:
            return ChatColor.YELLOW
        case 2:
            return ChatColor.RED
        case _:
            return ChatColor.WHITE

def fixedValue(data: dict[str, Any]):
    for key, value in data.items():
        if value.lower() == 'true':
            data[key] = True
        elif value.lower() == 'false':
            data[key] = False
        elif value.isdigit():
            data[key] = int(value)
        else:
            try:
                data[key] = float(value)
            except ValueError:
                pass
    return data

def get_data_content_type(obj: Any):
    if isinstance(obj, (list, tuple, dict)):
        return "application/json"
    else:
        return "text/plain"
    
def parse_obj_as_type(obj: Any, type_: Type[Any]) -> Any:
    if obj is None:
        return obj
    origin = getattr(type_, '__origin__', None)
    args = get_args(type_)
    if origin == Union:
        for arg in args:
            try:
                return parse_obj_as_type(obj, getattr(arg, '__origin__', arg))
            except:
                ...
        return None
    elif origin == dict:
        for arg in args:
            try:
                return parse_obj_as_type(obj, getattr(arg, '__origin__', arg))
            except:
                ...
        return load_params(obj, origin)
    elif origin == inspect._empty:
        return None
    elif origin == list:
        for arg in args:
            try:
                return [parse_obj_as_type(o, getattr(arg, '__origin__', arg)) for o in obj]
            except:
                ...
        return []
    elif origin is not None:
        return origin(obj)
    else:
        for arg in args:
            try:
                return parse_obj_as_type(obj, getattr(arg, '__origin__', arg))
            except:
                return arg(**load_params(obj, type_))
    try:
        return type_(**load_params(obj, type_))
    except:
        try:
            return load_params(obj, type_)
        except:
            return type_(obj)

def load_params(data: Any, type_: Any):
    value = {name: (value.default if value.default is not inspect._empty else None) for name, value in inspect.signature(type_).parameters.items() if not isinstance(value, inspect._empty)}
    if isinstance(data, dict):
        for k, v in data.items():
            value[k] = v
        return value
    else:
        return data
    
class MinecraftUtils:
    @staticmethod
    def getVarInt(data: int):
        r: bytes = b''
        while 1:
            if data & 0xFFFFFF80 == 0:
               r += data.to_bytes(1, 'big')
               break
            r += (data & 0x7F | 0x80).to_bytes(1, 'big')
            data >>= 7
        return r
    @staticmethod
    def getVarIntLength(data: int):
        return len(MinecraftUtils.getVarInt(data))
class DataOutputStream:
    def __init__(self, encoding: str = "utf-8") -> None:
        self.io = io.BytesIO()
        self.encoding = encoding
    def write(self, value: bytes | int):
        if isinstance(value, bytes):
            self.io.write(value)
        else:
            self.io.write((value + 256 if value < 0 else value).to_bytes()) # type: ignore
    def writeBoolean(self, value: bool):
        self.write(value.to_bytes())
    def writeShort(self, data: int):
        self.write(((data >> 8) & 0xFF).to_bytes())
        self.write(((data >> 0) & 0xFF).to_bytes())
    def writeInteger(self, data: int):
        self.write(((data >> 24) & 0xFF).to_bytes())
        self.write(((data >> 16) & 0xFF).to_bytes())
        self.write(((data >> 8) & 0xFF).to_bytes())
        self.write((data & 0xFF).to_bytes())
    def writeVarInt(self, value: int):
        self.write(MinecraftUtils.getVarInt(value))
    def writeString(self, data: str, encoding: Optional[str] = None):
        self.writeVarInt(len(data.encode(encoding or self.encoding)))
        self.write(data.encode(encoding or self.encoding))
    def writeLong(self, data: int):
        data = data - 2 ** 64 if data > 2 ** 63 - 1 else data
        self.write((data >> 56) & 0xFF)
        self.write((data >> 48) & 0xFF)
        self.write((data >> 40) & 0xFF)
        self.write((data >> 32) & 0xFF)
        self.write((data >> 24) & 0xFF)
        self.write((data >> 16) & 0xFF)
        self.write((data >> 8 ) & 0xFF)
        self.write((data >> 0 ) & 0xFF)
    def __sizeof__(self) -> int:
        return self.io.tell()
    def __len__(self) -> int:
        return self.io.tell()
class DataInputStream:
    def __init__(self, initial_bytes: bytes = b'', encoding: str = "utf-8") -> None:
        self.io = io.BytesIO(initial_bytes)
        self.encoding = encoding
    def read(self, __size: int | None = None):
        return self.io.read(__size)
    def readIntegetr(self):
        value = self.read(4)
        return ((value[0] << 24) + (value[1] << 16) + (value[2] << 8) + (value[3] << 0))
    def readBoolean(self):
        return bool(int.from_bytes(self.read(1)))
    def readShort(self):
        value = self.read(2)
        if value[0] | value[1] < 0:
            raise EOFError()
        return ((value[0] << 8) + (value[1] << 0))
    def readLong(self) -> int:
        value = list(self.read(8))
        value = (
            (value[0] << 56) +
            ((value[1] & 255) << 48) +
            ((value[2] & 255) << 40) +
            ((value[3] & 255) << 32) +
            ((value[4] & 255) << 24) +
            ((value[5] & 255) << 16) +
            ((value[6] & 255) << 8) +
            ((value[7] & 255) << 0))
        return value - 2 ** 64 if value > 2 ** 63 - 1 else value
    def readVarInt(self) -> int:
        i: int = 0
        j: int = 0
        k: int
        while 1:
            k = int.from_bytes(self.read(1))
            i |= (k & 0x7F) << j * 7
            j += 1
            if j > 5:raise RuntimeError("VarInt too big")
            if (k & 0x80) != 128: break
        return i - 2 ** 31 * 2 if i >= 2 ** 31 - 1 else i
    def readString(self, maximun: Optional[int] = None, encoding: Optional[str] = None) -> str:
        return self.read(self.readVarInt() if maximun == None else min(self.readVarInt(), max(maximun, 0))).decode(encoding or self.encoding)
    def readBytes(self, length: int) -> bytes:
        return self.read(length)

class FileDataInputStream(DataInputStream):
    def __init__(self, br: io.BufferedReader) -> None:
        super().__init__()
        self.io = br
    def read(self, __size: int | None = None):
        data = self.io.read(__size)
        if not data:
            raise EOFError(self.io)
        return data
class FileDataOutputStream(DataOutputStream):
    def __init__(self, bw: io.BufferedWriter) -> None:
        super().__init__()
        self.io = bw



