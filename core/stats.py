from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
import pyzstd as zstd

from core.utils import (
    DataInputStream,
    DataOutputStream,
    FileDataInputStream,
    get_timestamp_from_day_tohour,
    get_timestamp_from_hour_tohour,
)
from core.api import File
from core import timer as Timer

class UserAgent(Enum):
    PCL2 = "PCL2"
    PCL = "PCL"
    HMCL = "HMCL"
    POJAV = "PojavLauncher"
    FCL = "FCL"
    BAKAXL = "BakaXL"
    GOT = "got"
    BADLION = "Badlion Client"
    TECHNIC = "TechnicLauncher"   
    TLAUNCHER = "TLauncher"
    MULTIMC = "MultiMC"
    LUNAR = "Lunar Client"  
    MAGNET = "Magnet" 
    ATLAUNCHER = "ATLauncher" 
    CURSEFORGE = "CurseForge"
    DALVIK = "Dalvik"
    WARDEN = "bmclapi-warden"
    OPENBMCLAPI_CLUSTER = "openbmclapi-cluster"
    PYTHON = "python-openbmclapi"
    OTHER = "Other"
    @staticmethod
    def parse_ua(user_gent: str) -> list['UserAgent']:
        data = []
        for ua in user_gent.split(" "):
            ua = ua.split("/")[0]
            for UA in UserAgent:
                if UA.value == ua.lower():
                    data.append(UA)
        return data or [UserAgent.OTHER]
    @staticmethod
    def get_ua(ua: str) -> 'UserAgent':
        for _ in UserAgent:
            if _.value == ua:
                return _
        return UserAgent.OTHER

class GlobalStats:
    def __init__(self, ua: defaultdict['UserAgent', int] = defaultdict(int), ip: defaultdict[str, int] = defaultdict(int)):
        self.useragent: defaultdict['UserAgent', int] = ua
        self.ip: defaultdict[str, int] = ip

    def add_ua(self, ua: str = ""):
        for ua in UserAgent.parse_ua(ua):
            self.useragent[ua] += 1
    def add_ip(self, ip: str):
        self.ip[ip] += 1
    def get_binary(self):
        cache_ip = self.ip.copy()
        cache_ua = self.useragent.copy()
        buf = DataOutputStream()
        buf.writeVarInt(len(cache_ip))
        buf.writeVarInt(len(cache_ua))
        for ip, c in cache_ip.items():
            buf.writeString(ip)
            buf.writeVarInt(c)
        for ua, c in cache_ua.items():
            buf.writeString(ua.value)
            buf.writeVarInt(c)
        return zstd.compress(buf.io.getbuffer())
    @staticmethod
    def from_binary(data: bytes):
        input = DataInputStream(zstd.decompress(data))
        cache_ip: defaultdict['UserAgent', int] = defaultdict(int)
        cache_ua: defaultdict[str, int] = defaultdict(int)
        ip_length = input.readVarInt()
        ua_length = input.readVarInt()
        for _ in range(ip_length):
            cache_ip[input.readString()] = input.readVarInt()
        for _ in range(ua_length):
            cache_ua[UserAgent.get_ua(input.readString())] = input.readVarInt()
        return GlobalStats(cache_ip, cache_ua)
    def reset(self):
        self.useragent.clear()
        self.ip.clear()

globalStats = GlobalStats()

class StorageStats:
    def __init__(self, name) -> None:
        self._name = name
        self.reset()

    def hit(self, file: File, offset: int, ip: str, ua: str = ""):
        global globalStats
        byte = file.size - offset
        if file.cache:
            self._cache_hits += 1
            self._cache_bytes += byte
        else:
            self._hits += 1
            self._bytes += byte
        globalStats.add_ip(ip)
        globalStats.add_ua(ua)
        write_storage()

    def failed(self):
        self._failed += 1

    def __str__(self) -> str:
        return "StorageStats(name={}, hits={}, bytes={}, cache_hits={}, cache_bytes={}, total_hits={}, total_bytes={}, failed={}, last_hits={}, last_bytes={})".format(
            self._name,
            self._hits,
            self._bytes,
            self._cache_hits,
            self._cache_bytes,
            self.get_total_hits(),
            self.get_total_bytes(),
            self._failed,
            self._last_hits,
            self._last_bytes,
        )

    def __repr__(self) -> str:
        return self.__str__()

    def get_total_hits(self):
        return self._hits + self._cache_hits

    def get_total_bytes(self):
        return self._bytes + self._cache_bytes

    def get_name(self):
        return self._name

    def reset(self):
        self._hits = 0
        self._bytes = 0
        self._cache_hits = 0
        self._cache_bytes = 0
        self._failed = 0
        self._last_hits = 0
        self._last_bytes = 0

    def get_last_hits(self):
        return self._last_hits

    def get_last_bytes(self):
        return self._last_bytes

    def add_last_hits(self, hits):
        self._last_hits += hits

    def add_last_bytes(self, bytes):
        self._last_bytes += bytes


@dataclass
class SyncStorage:
    sync_hits: int
    sync_bytes: int
    object: StorageStats


storages: dict[str, StorageStats] = {}
cache: Path = Path("./cache")
cache.mkdir(exist_ok=True, parents=True)
last_storages: dict[str, int] = {}
last_ip: dict[str, int] = {}
last_ua: int = 0
last_hour: int = 0
db: sqlite3.Connection = sqlite3.Connection("./cache/stats.db", check_same_thread=False)
db.execute(
    """
CREATE TABLE IF NOT EXISTS access (  
    hour unsigned bigint NOT NULL,
    storage TEXT NOT NULL,  
    hit unsigned bigint NOT NULL DEFAULT 0,
    bytes unsigned bigint NOT NULL DEFAULT 0,
    cache_hit unsigned bigint NOT NULL DEFAULT 0,
    cache_bytes unsigned bigint NOT NULL DEFAULT 0,
    last_hit unsigned bigint NOT NULL DEFAULT 0,
    last_bytes unsigned bigint NOT NULL DEFAULT 0,
    failed unsigned bigint NOT NULL DEFAULT 0
);"""
)
db.execute(
    """
CREATE TABLE IF NOT EXISTS g_access_ip (  
    hour unsigned bigint NOT NULL,
    ip TEXT NOT NULL,
    hit unsigned bigint not null default 0
);"""
)
db.execute(
    """
CREATE TABLE IF NOT EXISTS g_access_ua (  
    hour unsigned bigint NOT NULL
);"""
)

db.commit()


def read_storage():
    global storages, last_hour
    if (
        not Path("./cache/storage.bin").exists()
        or Path("./cache/storage.bin").stat().st_size == 0
    ):
        return
    with open("./cache/storage.bin", "rb") as r:
        f = FileDataInputStream(r)
        last_hour = f.readVarInt()
        GlobalStats.from_binary(f.read(f.readVarInt()))
        for _ in range(f.readVarInt()):
            storage = StorageStats(f.readString())
            (
                storage._hits,
                storage._bytes,
                storage._cache_hits,
                storage._cache_bytes,
                storage._failed,
                storage._last_hits,
                storage._last_bytes,
            ) = (
                f.readVarInt(),
                f.readVarInt(),
                f.readVarInt(),
                f.readVarInt(),
                f.readVarInt(),
                f.readVarInt(),
                f.readVarInt(),
            )

            storages[storage.get_name()] = storage


def write_storage():
    global storages, globalStats
    f = DataOutputStream()
    f.writeVarInt(last_hour)
    data = globalStats.get_binary()
    f.writeVarInt(len(data))
    f.write(data)
    f.writeVarInt(len(storages))
    for storage in storages.values():
        f.writeString(storage.get_name())
        for field in (
            storage._hits,
            storage._bytes,
            storage._cache_hits,
            storage._cache_bytes,
            storage._failed,
            storage._last_hits,
            storage._last_bytes,
        ):
            f.writeVarInt(field)
    with open("./cache/storage.bin", "wb") as w:
        w.write(f.io.getbuffer())


def get_offset_storages() -> list[SyncStorage]:
    sync_storages = []
    for storage in storages.values():
        sync_storage = SyncStorage(
            storage.get_total_hits() - storage.get_last_hits(),
            storage.get_total_bytes() - storage.get_last_bytes(),
            storage,
        )
        if sync_storage.sync_hits == 0 and sync_storage.sync_bytes == 0:
            continue
        sync_storages.append(sync_storage)
    return sync_storages


def get_storage(name):
    global storages
    if name not in storages:
        storages[name] = StorageStats(name)
    return storages[name]


def _write_database():
    global last_storages, last_hour, globalStats, last_ip, last_ua
    cmds: list[tuple[str, tuple[Any, ...]]] = []
    hour = get_hour(0)
    for storage in storages.values():
        if (hour not in last_storages or hour != last_storages[storage.get_name()]) and not exists(
            "select storage from access where storage = ? and hour = ?",
            storage.get_name(),
            hour,
        ):
            cmds.append(
                (
                    "insert into access(storage, hour) values (?, ?)",
                    (storage.get_name(), hour),
                )
            )
            last_storages[storage.get_name()] = hour
        cmds.append(
            (
                "update access set hit = ?, bytes = ?, cache_hit = ?, cache_bytes = ?, last_hit = ?, last_bytes = ?, failed = ? where storage = ? and hour = ?",
                (
                    storage._hits,
                    storage._bytes,
                    storage._cache_hits,
                    storage._cache_bytes,
                    storage._last_hits,
                    storage._last_bytes,
                    storage._failed,
                    storage.get_name(),
                    hour,
                ),
            )
        )
    for ip, c in globalStats.ip.items():
        if (ip not in last_ip or hour != last_ip[ip]) and not exists(
            "select ip from g_access_ip where ip = ? and hour = ?",
            ip,
            hour,
        ):
            cmds.append(
                (
                    "insert into g_access_ip(ip, hour) values (?, ?)",
                    (ip, hour),
                )
            )
            last_ip[ip] = hour
        cmds.append(
            (
                "update g_access_ip set hit = ? where ip = ? and hour = ?",
                (
                    c,
                    ip,
                    hour
                )
            )
        )
    if last_ua != hour and not exists(
            "select hour from g_access_ua where hour = ?",
            hour,
        ):
            cmds.append(
                (
                    "insert into g_access_ua(hour) values (?)",
                    (hour,),
                )
            )
            last_ua = hour
    for ua, c in globalStats.useragent.items():
        cmds.append(
            (
                f"update g_access_ua set `{ua.value}` = ? where hour = ?",
                (
                    c,
                    hour
                )
            )
        )
        
    executemany(*cmds)
    if last_hour and last_hour != hour:
        for storage in storages.values():
            storage.reset()
        globalStats.reset()
    last_hour = hour


def get_hour(hour: int) -> int:
    return int(get_timestamp_from_hour_tohour(hour))


def execute(cmd: str, *params) -> None:
    global db
    db.execute(cmd, params)
    db.commit()


def executemany(*cmds: tuple[str, tuple[Any, ...]]) -> None:
    global db
    for cmd in cmds:
        db.execute(*cmd)
    db.commit()


def query(cmd: str, *params) -> list[Any]:
    global db
    cur = db.execute(cmd, params)
    return cur.fetchone() or []


def queryAllData(cmd: str, *params) -> list[tuple]:
    global db
    cur = db.execute(cmd, params)
    return cur.fetchall() or []


def exists(cmd: str, *params) -> bool:
    return len(query(cmd, *params)) != 0


def columns(table):
    return [q[0] for q in queryAllData(f"SHOW COLUMNS FROM {table}")]


def addColumns(table, params, data, default=None):
    #if params not in columns(table):
    try:
        execute(f"ALTER TABLE {table} ADD COLUMN {params} {data}")
        if default is not None:
            execute(f"UPDATE {table} SET {params}={default}")
    except:
        ...


def get_storage_stats():
    storage: dict[str, dict[str, int]] = {}
    t = get_timestamp_from_day_tohour(30)
    for r in queryAllData(
        "select storage, hour, hit, bytes, cache_hit, cache_bytes, last_hit, last_bytes, failed from access where hour >= ?",
        t,
    ):
        if r[0] not in storage:
            storage[r[0]] = {
                "cache_hits": 0,
                "cache_bytes": 0,
                "hits": 0,
                "bytes": 0,
                "last_hits": 0,
                "last_bytes": 0,
                "failed": 0,
            }
        storage[r[0]]["cache_hits"] = r[4]
        storage[r[0]]["cache_bytes"] = r[5]
        storage[r[0]]["hits"] = r[2]
        storage[r[0]]["bytes"] = r[3]
        storage[r[0]]["last_hits"] = r[6]
        storage[r[0]]["last_bytes"] = r[7]
        storage[r[0]]["failed"] = r[8]
    return storage


def hourly():
    data = []
    hours: dict[int, StorageStats] = {}
    t = get_timestamp_from_day_tohour(0)
    for r in queryAllData(
        "select storage, hour, hit, bytes, cache_hit, cache_bytes, last_hit, last_bytes, failed from access where hour >= ?",
        t,
    ):
        hour = int(r[1] - t)
        if hour not in hours:
            hours[hour] = StorageStats("Total")
        hours[hour]._hits += r[2]
        hours[hour]._bytes += r[3]
        hours[hour]._cache_hits += r[4]
        hours[hour]._cache_bytes += r[5]
        hours[hour]._last_hits += r[6]
        hours[hour]._last_bytes += r[7]
        hours[hour]._failed += r[8]
    for hour in sorted(hours.keys()):
        data.append(
            {
                "_hour": int(hour),
                "hits": hours[hour]._hits,
                "bytes": hours[hour]._bytes,
                "cache_hits": hours[hour]._cache_hits,
                "cache_bytes": hours[hour]._cache_bytes,
                "last_hits": hours[hour]._last_hits,
                "last_bytes": hours[hour]._last_bytes,
                "failed": hours[hour]._failed,
            }
        )
    return data


def daily():
    data = []
    t = get_timestamp_from_day_tohour(30)
    days: dict[int, StorageStats] = {}
    for r in queryAllData(
        "select storage, hour, hit, bytes, cache_hit, cache_bytes, last_hit, last_bytes, failed from access where hour >= ?",
        t,
    ):
        hour = (r[1] - t) // 24
        if hour not in days:
            days[hour] = StorageStats("Total")
        days[hour]._hits += r[2]
        days[hour]._bytes += r[3]
        days[hour]._cache_hits += r[4]
        days[hour]._cache_bytes += r[5]
        days[hour]._last_hits += r[6]
        days[hour]._last_bytes += r[7]
        days[hour]._failed += r[8]
    for day in sorted(days.keys()):
        data.append(
            {
                "_day": int(day),
                "hits": days[day]._hits,
                "bytes": days[day]._bytes,
                "cache_hits": days[day]._cache_hits,
                "cache_bytes": days[day]._cache_bytes,
                "last_hits": days[day]._last_hits,
                "last_bytes": days[day]._last_bytes,
                "failed": days[day]._failed,
            }
        )
    return data


for ua in UserAgent:
    addColumns("g_access_ua", f"`{ua.value}`", " unsigned bigint NOT NULL DEFAULT 0")

read_storage()
_write_database()


def init():
    Timer.delay(write_database, delay=time.time() % 1)


def write_database():
    while int(os.environ["ASYNCIO_STARTUP"]):
        _write_database()
        time.sleep(1)
