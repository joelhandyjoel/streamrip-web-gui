![streamrip web interface](https://github.com/AnOddName/streamrip-web-gui/blob/main/demo/home_screen.png?raw=true)

# Streamrip Web GUI

A web interface for [Streamrip](https://github.com/nathom/streamrip), providing a GUI for downloading music from various streaming services. 

Streamrip is lit but CLI-only. Having to SSH into my stupid little server each time I wanted to download a track was too much effort for me. 
(Mainly Quboz for me low key I don't even know if Tidal/Deezer work because I don't have accounts for them)

Intended to be used for Docker/Docker-Compose but you can run it locally too.

![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-green.svg)

## Features

- **Multi-Service Support**: Download from Qobuz, Tidal, Deezer, SoundCloud
- **Built-in Search**: Search and download directly from the web interface
- **Download Management**: Track active downloads, view history, and browse downloaded files
- **Configuration Editor**: Edit streamrip settings directly from the web interface
- **Docker Ready**: Easy deployment with Docker

## Screenshots

![search](https://github.com/AnOddName/streamrip-web-gui/blob/main/demo/search.png?raw=true)
![download](https://github.com/AnOddName/streamrip-web-gui/blob/main/demo/active_dl.png?raw=true)

## Prerequisites

- Python 3.11+ (if running without Docker)
- Docker and Docker Compose (for containerized deployment)
- Valid streaming service credentials configured in streamrip

## Installation

You MUST install and configure Streamrip first.

1. Install Streamrip:
```bash
pip install streamrip
```

2. Configure Streamrip:
```bash
rip config
```
Follow the [Streamrip configuration guide](https://github.com/nathom/streamrip/wiki/Configuration) to set up your credentials.

### Option 1: Pre-built workflow. 
1: Add this to your `docker-compose.yml`

```
streamrip:
    build: .
    image: joelhandyjoel/streamrip-web-gui:latest
    container_name: streamrip-web

    environment:
      PUID: 1000
      PGID: 1000
      STREAMRIP_CONFIG_DIR: /config
      STREAMRIP_DOWNLOAD_DIR: /music
      MAX_CONCURRENT_DOWNLOADS: 1

    volumes:
      - /mnt/docker/streamrip:/config
      - /mnt/media/Music:/music

    ports:
      - "5002:5000"

    restart: unless-stopped
```

2: run with `docker-compose up`

3: Access the web interface at `http://localhost:5002`

### Option 2: Docker

1. Clone the repository:
```bash
git clone https://github.com/anoddname/streamrip-web-gui.git
cd streamrip-web
```

2. Create a `docker-compose.yml` file:
```yaml
version: "3.8"

services:
  streamrip:
    build: .
    image: joelhandyjoel/streamrip-web-gui:latest
    container_name: streamrip-web

    environment:
      PUID: 1000
      PGID: 1000
      STREAMRIP_CONFIG_DIR: /config
      STREAMRIP_DOWNLOAD_DIR: /music
      MAX_CONCURRENT_DOWNLOADS: 1

    volumes:
      - /mnt/docker/streamrip:/config
      - /mnt/media/Music:/music

    ports:
      - "5002:5000"

    restart: unless-stopped
```

3. Build and run:
```bash
docker-compose up -d --build
```

4. Access the web interface at `http://localhost:5002`

### Option 3: Manual Installation

1. Clone this repository:
```bash
git clone https://github.com/anoddname/streamrip-web.git
cd streamrip-web
```

2. Install dependencies:
```bash
pip install flask gunicorn requests
```

3. Run the application:
```bash
python app.py
```

## Configuration

### Streamrip Configuration

Before using Streamrip Web, you need to configure streamrip with your streaming service credentials:

1. **Qobuz**: Requires email and password (or TOKEN)
2. **Tidal**: Requires email and password  
3. **Deezer**: Requires ARL
4. **SoundCloud**: Works without authentication

Check the [Streamrip documentation](https://github.com/nathom/streamrip/wiki) for instructions.

## Usage

### Downloading from URL

1. Paste a streaming service URL in the input field
2. Select quality (MP3 128/320 or FLAC 16/24-bit)
3. Click DOWNLOAD

### Searching for Music

1. Select a streaming service from the dropdown
2. Choose search type (Albums, Tracks, or Artists)
3. Enter your search query
4. Click on DOWNLOAD next to any result

- Searches will use the QUALITY from the URL paste dropdown.

## Troubleshooting

### Common Issues

1. **"Config file not found"**: Make sure streamrip is properly configured. Run `rip config` to create a configuration file. Also check locations.

2. **Downloads failing/Searches timing out**: Check that your streaming service credentials are valid and properly configured in streamrip. Tidal will timeout, Deezer will throw errors.

3. **Downloads disappearing from Active DL/History tabs**:  The files were still prolly downloaded, dont worry about it I'll fix it later it was pissing me off

4. **No images when run locally**: CORS issue

5. Unable to open database file/Failed to parse JSON Error: This occurs when the config file inside the container has wrong paths. Fix it with:
```bash
docker exec -it streamrip /bin/bash
sed -i 's|/home/YOURUSERNAME/StreamripDownloads|/music|g' /config/streamrip/config.toml
sed -i 's|/home/YOURUSERNAME/.config/streamrip/|/config/streamrip/|g' /config/streamrip/config.toml
exit
```
  Note to replace `YOURUSERNAME` with, you guessed it, your username.


## Disclaimer

This tool is for educational purposes only. Ensure you comply with the terms of service of the streaming platforms you use. Support artists by purchasing their music.

---


Fueled by spite









