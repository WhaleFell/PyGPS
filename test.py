from typing import Iterator
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOTPATH: Path = Path(__file__).resolve().parent
GPSFILEDIR: Path = Path(ROOTPATH, "data")
FILENAME = "gps_2024_02_04.csv"
GPSFILE = Path(GPSFILEDIR, FILENAME)


if not GPSFILEDIR.exists():
    GPSFILEDIR.mkdir()

KML_FORMAT = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" 
	xmlns:atom="http://www.w3.org/2005/Atom" 
	xmlns:gx="http://www.google.com/kml/ext/2.2" 
	>
	<Document>
		<name> GPS Track </name>
		<Placemark>
			<name>[name]</name>
			<Style>
				<LineStyle><color>7fff0000</color><width>5</width></LineStyle>
			</Style>
				<gx:Track>
					<gx:coord>113.03961419 23.01465949 1</gx:coord>
					<when>2023-07-19T11:52:10Z</when>
                    [datas]
                </gx:Track>
		</Placemark>
	</Document>
</kml>
"""


# make kml string use kml format
def make_kml(data: str, name: str = "default") -> str:
    return KML_FORMAT.replace("[datas]", data).replace("[name]", name)


# second timestamp to utc+8 datetime
def timestamp_to_datetime(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone(timedelta(hours=8)))


# datetime convert to 2023-07-19T11:52:10Z format
def datetime_to_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_to_str(timestamp) -> str:
    timestamp = int(timestamp)
    dt = timestamp_to_datetime(timestamp)
    return datetime_to_str(dt)


def read_gps_data(filepath: Path) -> Iterator[dict]:
    """read GPS data in csv file and than return iterator
    CSV file format:
    timestamp,latitude,longitude,altitude,speed

    return: dict
    {
        "timestamp": timestamp,
        "latitude": latitude,
        "longitude": longitude,
        "altitude": altitude,
        "speed": speed
    }
    """
    with open(filepath, "r", encoding="utf8") as f:
        for row in f:
            data_lst = row.strip().split(",")
            data_dict = {
                "timestamp": data_lst[0],
                "latitude": data_lst[1],
                "longitude": data_lst[2],
                "altitude": data_lst[3],
                "speed": data_lst[4],
            }
            yield data_dict


def main():
    kml_data = ""
    for data in read_gps_data(GPSFILE):
        # print(data)
        kml_data += f"<gx:coord>{data['longitude']} {data['latitude']} {data['altitude']}</gx:coord><when>{timestamp_to_str(data['timestamp'])}</when>"

    kml = make_kml(kml_data)

    with open(Path(GPSFILEDIR, "gps.kml"), "w", encoding="utf8") as f:
        f.write(kml)


if __name__ == "__main__":
    main()
