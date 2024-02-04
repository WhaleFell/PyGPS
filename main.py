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

load_dotenv(override=True)


################# Config ##################
SERIAL: str = os.getenv("SERIAL", "COM6")
BAUDRATE: int = int(os.getenv("BAUDRATE", 115200))
API_URL: str = os.getenv("API_URL", "http://localhost:8000")  # without last "/"
ROOTPATH: Path = Path(__file__).resolve().parent
GPSFILEDIR: Path = Path(ROOTPATH, "data")
TRIGGER_STOP_TIME: int = int(os.getenv("TRIGGER_STOP_TIME", 20))  # 300 seconds
TRIGGER_STOP_SPEED: float = float(os.getenv("TRIGGER_STOP_SPEED", 0.5))  # 0.5 km/h
################# Config End ##############

if not GPSFILEDIR.exists():
    GPSFILEDIR.mkdir()
ser = None
error_count = 0
max_error_count = 5
upload_queue = asyncio.Queue()
Aclient = httpx.AsyncClient(
    verify=False,
    timeout=5,
)

######### Helper Functions ############


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
                except Exception as e:
                    print(f"retry {i+1} times: {e}")
                    await asyncio.sleep(interval)

        return wrapper

    return decorator


# generate GPS file subfix with date like gps_2024-02-02.csv
def gen_gps_filepath() -> Path:
    filename = datetime.datetime.now().strftime("gps_%Y_%m_%d.csv")
    return Path(GPSFILEDIR, filename)


######### Helper Functions End ############


async def init():
    global ser
    print("initialization...")
    while True:
        try:
            ser = serial.Serial(SERIAL, 115200, timeout=5)
            return
        except Exception as e:
            print(f"init serial error: {e} retry in 2s...")
            await asyncio.sleep(2)


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
        try:
            line = ser.readline().decode("utf-8")  # type: ignore
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
            print("positioning...", end="\r")

    print(f"success get gps data: {data}")
    return data


@aretry(times=3, interval=0.5)
async def upload_gps_data(data: dict):
    """upload formatted gps data to HTTP server"""
    response = await Aclient.post(f"{API_URL}/gps/upload/", json=data)
    print(f"upload success: {response.text}")
    await response.aclose()


async def save_gps_data(data: dict):
    """save formatted gps data to local file

    format: timestamp,latitude,longitude,altitude,speed
    CSV format
    """
    async with aiofiles.open(gen_gps_filepath().as_posix(), "a", encoding="utf8") as f:
        await f.write(
            f"{data['GPSTimestamp']},{data['latitude']},{data['longitude']},{data['altitude']},{data['speed']}\n"
        )


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

        if not ignore:
            await upload_queue.put(data)

        await asyncio.sleep(1)


async def handle_gps_loop():
    datas = []
    while True:
        data = await upload_queue.get()
        datas.append(data)
        # await upload_gps_data(data)
        # await save_gps_data(data)
        # use asyncio.ensure_future to avoid blocking. Tasks will run together.
        await asyncio.gather(*[upload_gps_data(data), save_gps_data(data)])
        upload_queue.task_done()


async def main():
    await init()
    tasks = [get_gps_loop(), handle_gps_loop()]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
    if isinstance(ser, serial.Serial):
        ser.close()
