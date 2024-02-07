# Python GPS

Use Python to get GPS data from a GPS module via serial port (UART protocol).

## Development

create a virtual environment and install the requirements:

```bash
# build python virtual envirement
python -m venv ./venv
# If your have multiple python version,use specific python version to build virtual envirement
py --list # list all python version
py -3.11 -m venv ./venv

# Set Powershell Limited
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Activate virtual envirement
.\venv\Scripts\Activate.ps1

# update pip version
python -m pip install --upgrade pip --index-url=https://pypi.org/simple

# install require model
pip install --upgrade -r requirements.txt --index-url=https://pypi.org/simple

# install test require model
pip install --upgrade -r requirements-test.txt --index-url=https://pypi.org/simple
```

## Systemctl

```bash
# show log
journalctl -f -u gps 

# restart service
sudo systemctl restart gps
```

## Library
