#!/usr/bin/env python
# -*-coding:utf-8 -*-
"""
@File    :   main.py
@Time    :   2024/02/02 11:34:07
@Author  :   WhaleFall
@License :   (C)Copyright 2020-2023, WhaleFall
@Desc    :   
Base on Python asynchrone to read GPS data from serial port.
And upload formatted data to HTTP server.
"""
import serial
import serial.tools.list_ports
import pynmea2
import asyncio
import os
import httpx
from dotenv import load_dotenv
import datetime
from functools import wraps
from pathlib import Path
import aiofiles
from typing import Union, List, Never

load_dotenv(override=True)


################# Config ##################
SERIAL: str = os.getenv("SERIAL", "COM6")
BAUDRATE: int = int(os.getenv("BAUDRATE", 115200))
API_URL: str = os.getenv("API_URL", "http://localhost:8000")  # without last "/"
ROOTPATH: Path = Path(__file__).resolve().parent
GPSFILEDIR: Path = Path(ROOTPATH, "data")
DISABLE_IGNORE_STOP: bool = bool(os.getenv("DISABLE_IGNORE_STOP", False))
TRIGGER_STOP_TIME: int = int(os.getenv("TRIGGER_STOP_TIME", 20))  # 300 seconds
TRIGGER_STOP_SPEED: float = float(os.getenv("TRIGGER_STOP_SPEED", 0.5))  # 0.5 km/h
# number of GPS data per upload
NUM_PER_UPLOAD: int = int(os.getenv("NUM_PER_UPLOAD", 100))
CHECK_NETWORK_INTERVAL: int = int(os.getenv("CHECK_NETWORK_INTERVAL", 5))  # 10 seconds
DEBUG: bool = bool(os.getenv("DEBUG", False))
################# Config End ##############

if not GPSFILEDIR.exists():
    GPSFILEDIR.mkdir()
ser = None
ser_readline = None
error_count = 0
max_error_count = 5
is_network_available = None
upload_queue = asyncio.Queue()
Aclient = httpx.AsyncClient(
    verify=False,
    timeout=8,
)
# prefix with / and suffix with /, like /gps/upload/
API_ROUTES = {
    "gps": "/gps/upload/",
    "mutil_gps": "/gps/upload/multi/",
    "ping": "/ping/",
}

######### Helper Functions ############

raw_print = print


# https://stackoverflow.com/questions/29557353/how-can-i-improve-pyserial-read-speed/56240817#56240817
# https://github.com/pyserial/pyserial/issues/216
class ReadLine:
    def __init__(self, s: serial.Serial) -> None:
        self.buf = bytearray()
        self.s = s

    def readline(self) -> bytearray:
        i = self.buf.find(b"\n")
        if i >= 0:
            r = self.buf[: i + 1]
            self.buf = self.buf[i + 1 :]
            return r
        while True:
            i = max(1, min(2048, self.s.in_waiting))
            data = self.s.read(i)
            i = data.find(b"\n")
            if i >= 0:
                r = self.buf + data[: i + 1]
                self.buf[0:] = data[i + 1 :]
                return r
            else:
                self.buf.extend(data)


def print(msg, *args, **kwargs):
    """overwrite print function in order to optimize"""
    if DEBUG:
        raw_print(msg, *args, **kwargs)


def list_devices():
    ports = serial.tools.list_ports.comports()
    for port, desc, hwid in sorted(ports):
        print("{}: {} [{}]".format(port, desc, hwid))


# knots convert to km/h
def knots_to_kmh(knots: float) -> float:
    return knots * 1.852


# safely get object attribute
def safe_getattr(obj, attr, default=None):
    try:
        return getattr(obj, attr)
    except AttributeError:
        return default


# decide if the dict attribute is None
def dict_is_none(data: dict) -> bool:
    for k, v in data.items():
        if v is None:
            return True
    return False


# raise error and count
def raise_error(e: Exception):
    global error_count
    error_count += 1
    if error_count > max_error_count:
        raise e


# according to datetime.time and datetime.date to get timestamp
def get_timestamp(time: datetime.time, date: datetime.date) -> int:
    return int(datetime.datetime.combine(date, time).timestamp())


# async function retry decorator
def aretry(times: int = 3, interval: float = 1):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for i in range(times):
                try:
                    return await func(*args, **kwargs)
                except httpx.HTTPError as e:
                    print(f"HTTP connect error: {e} retry {i+1} times.")
                except Exception as e:
                    print(f"retry {i+1} times, error: {e}")

                await asyncio.sleep(interval)

        return wrapper

    return decorator


# generate GPS file subfix with date like gps_2024-02-02.csv
def gen_gps_filepath() -> Path:
    filename = datetime.datetime.now().strftime("gps_%Y_%m_%d.csv")
    return Path(GPSFILEDIR, filename)


# check network task: check if the network is available
async def check_network_task() -> Never:
    global is_network_available
    while True:
        try:
            response = await Aclient.get(f"{API_URL}{API_ROUTES['ping']}")
            await response.aclose()
            is_network_available = True
            print("network is available")
        except Exception as e:
            print(f"check network error: {e}")
            is_network_available = False

        await asyncio.sleep(CHECK_NETWORK_INTERVAL)


######### Helper Functions End ############


async def init():
    global ser, ser_readline
    print("initialization...")
    while True:
        try:
            ser = serial.Serial(SERIAL, 115200, timeout=1)
            ser_readline = ReadLine(ser)
            return
        except Exception as e:
            print(f"init serial error: {e} retry in 5s...")
            await asyncio.sleep(5)


async def get_gps_data() -> dict:
    """get gps data from serial port`

    Returns:
        formatted gps data:
        {
            "latitude": 23.4,
            "longitude": 113.3,
            "altitude": 0, # m
            "speed": 0, # km/h
            "GPSTimestamp": 0,
        }
    """
    data = {
        "latitude": None,
        "longitude": None,
        "altitude": None,
        "speed": None,
        "GPSTimestamp": None,
    }
    while dict_is_none(data):
        if ser_readline is None or ser is None:
            print("ser_readline is None, retry in 1s...")
            await asyncio.sleep(1)
            continue
        try:
            # the cache is cleared every minute
            if datetime.datetime.now().second == 0 and ser_readline.buf:
                ser_readline.buf.clear()
                ser.reset_input_buffer()
                print("clear cache...")
            line = ser_readline.readline().decode("utf-8")  # type: ignore
            msg = pynmea2.parse(line)
        except pynmea2.ParseError as e:
            print(f"nmea parse error: {e}")
            raise_error(e)
            await asyncio.sleep(1)
            continue
        except serial.SerialException as e:
            print(f"serial error: {e}")
            raise_error(e)
            await asyncio.sleep(1)
            continue
        except Exception as e:
            print(f"get GPS data error: {e}")
            raise_error(e)
            await asyncio.sleep(1)
            continue

        if isinstance(msg, pynmea2.GGA):
            data["altitude"] = safe_getattr(msg, "altitude")
        elif isinstance(msg, pynmea2.RMC):
            latitude = safe_getattr(msg, "latitude")
            longitude = safe_getattr(msg, "longitude")
            if latitude is not None and longitude is not None:
                data["latitude"] = round(latitude, 7)  # type: ignore
                data["longitude"] = round(longitude, 7)  # type: ignore

            timestamp = safe_getattr(msg, "timestamp")
            datestamp = safe_getattr(msg, "datestamp")
            if timestamp is not None and datestamp is not None:
                data["GPSTimestamp"] = get_timestamp(timestamp, datestamp)  # type: ignore

        elif isinstance(msg, pynmea2.VTG):
            data["speed"] = safe_getattr(msg, "spd_over_grnd_kmph")

        else:
            # print("positioning...", end="\r")
            pass

        # await asyncio.sleep(0.1)

    print(f"success get gps data: {data}")
    return data


@aretry(times=3, interval=0.5)
async def upload_gps_data(data: Union[dict, List[dict]]):
    """upload formatted gps data to HTTP server"""
    global is_network_available, Aclient
    if not is_network_available and is_network_available is not None:
        print("network is not available, upload stop...")
        return

    if isinstance(data, dict):
        response = await Aclient.post(f"{API_URL}{API_ROUTES['gps']}", json=data)
        print(f"upload success: {response.status_code} {response.text}")
        await response.aclose()
    elif isinstance(data, list):
        response = await Aclient.post(f"{API_URL}{API_ROUTES['mutil_gps']}", json=data)
        print(f"upload multiple success: {response.status_code} {response.text}")
        await response.aclose()


async def upload_store_gps_data():
    """upload stored gps data to HTTP server"""
    datas = await read_gps_data(gen_gps_filepath())
    lst = []
    if datas is None:
        return
    for data in datas:
        if data is None:
            continue
        # upload data when the number of data count > NUM_PER_UPLOAD
        lst.append(data)
        if len(lst) >= NUM_PER_UPLOAD:
            await upload_gps_data(lst)
            lst.clear()

    # upload remaining data
    if lst:
        await upload_gps_data(lst)
        lst.clear()

    print("upload store gps success")


async def save_gps_data(data: dict):
    """save formatted gps data to local file

    format: timestamp,latitude,longitude,altitude,speed
    CSV format
    """
    async with aiofiles.open(gen_gps_filepath().as_posix(), "a", encoding="utf8") as f:
        await f.write(
            f"{data['GPSTimestamp']},{data['latitude']},{data['longitude']},{data['altitude']},{data['speed']}\n"
        )


async def read_gps_data(filepath: Path) -> Union[None, list[dict]]:
    """read GPS data in csv file and than return datas
    CSV file format:
    timestamp,latitude,longitude,altitude,speed

    return: dict
    {
        "GPSTimestamp": timestamp,
        "latitude": latitude,
        "longitude": longitude,
        "altitude": altitude,
        "speed": speed
    }
    """
    if not filepath.exists():
        return None

    lst = []
    async with aiofiles.open(filepath.as_posix(), "r", encoding="utf8") as f:
        lines = await f.readlines()
        for line in lines:
            data_lst = line.strip().split(",")
            data_dict = {
                "GPSTimestamp": int(data_lst[0]),
                "latitude": float(data_lst[1]),
                "longitude": float(data_lst[2]),
                "altitude": float(data_lst[3]),
                "speed": float(data_lst[4]),
            }
            lst.append(data_dict)

    return lst


async def get_gps_loop():
    ignore = False
    stopping = False
    stop_timestamp = 0
    while True:
        data = await get_gps_data()

        # if speed is less than `TRIGGER_STOP_SPEED` km/h for 5 minutes, ignore it
        if data["speed"] < TRIGGER_STOP_SPEED:
            if not stopping:
                stop_timestamp = data["GPSTimestamp"]  # first stop timestamp
                stopping = True
                print(f"first stop at {stop_timestamp}")
            else:
                print(
                    f"had stopped...{data['GPSTimestamp'] - stop_timestamp} seconds..."
                )
                if data["GPSTimestamp"] - stop_timestamp > TRIGGER_STOP_TIME:
                    print(f"had stopped for {TRIGGER_STOP_TIME} seconds, ignore it")
                    ignore = True
        else:
            print("moving...")
            stopping = False
            ignore = False

        if (not ignore) or DISABLE_IGNORE_STOP:
            await upload_queue.put(data)

        await asyncio.sleep(1)


async def handle_gps_loop():
    while True:
        queue_size = upload_queue.qsize()
        print("remain data in queue:", queue_size)
        if queue_size >= 10:
            print("queue is large, upload data...")
            lst = []
            for _ in range(queue_size):
                data = await upload_queue.get()
                lst.append(data)
                await save_gps_data(data)
                upload_queue.task_done()
            asyncio.ensure_future(upload_gps_data(lst))
            lst.clear()
            continue

        data = await upload_queue.get()
        # await asyncio.gather(*[upload_gps_data(data), save_gps_data(data)])
        await save_gps_data(data)
        asyncio.ensure_future(upload_gps_data(data))
        upload_queue.task_done()


async def main():
    asyncio.ensure_future(check_network_task())
    asyncio.ensure_future(upload_store_gps_data())

    await init()
    tasks = [get_gps_loop(), handle_gps_loop()]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    # in python 3.9 if use asyncio.run(main()) will raise Queue loop attached to a different loop error
    # asyncio.run(main())
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    if isinstance(ser, serial.Serial):
        ser.close()
