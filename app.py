import os
import json
import time
import queue
import threading
import tempfile
import subprocess
import logging
import re
import requests
import shutil
import sqlite3

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    Response,
    stream_with_context,
)
from functools import wraps

# ------------------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------------------
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "false").lower() == "true"
AUTH_USER = os.environ.get("AUTH_USER", "")
AUTH_PASS = os.environ.get("AUTH_PASS", "")


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not AUTH_ENABLED:
            return fn(*args, **kwargs)

        auth = request.authorization
        if not auth or auth.username != AUTH_USER or auth.password != AUTH_PASS:
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Streamrip"'},
            )

        return fn(*args, **kwargs)

    return wrapper


# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# App setup
# ------------------------------------------------------------------------------
app = Flask(__name__)

DOWNLOADS_DB = "/config/streamrip/downloads.db"
STREAMRIP_CONFIG = os.environ.get("STREAMRIP_CONFIG", "/config/streamrip/config.toml")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/music")
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "2"))

download_queue = queue.Queue()
active_downloads = {}
download_history = []
sse_clients = []


@app.before_request
def enforce_auth():
    if not AUTH_ENABLED:
        return

    # Allow static files
    if request.path.startswith("/static"):
        return

    # Allow SSE (EventSource does not send auth headers reliably)
    if request.path == "/api/events":
        return

    auth = request.authorization
    if not auth or auth.username != AUTH_USER or auth.password != AUTH_PASS:
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Streamrip"'},
        )


# ------------------------------------------------------------------------------
# SSE helpers
# ------------------------------------------------------------------------------
def broadcast_sse(data):
    msg = f"data: {json.dumps(data)}\n\n"
    dead = []

    for q in sse_clients:
        try:
            q.put(msg)
        except Exception:
            dead.append(q)

    for q in dead:
        sse_clients.remove(q)


@app.route("/api/events")
def sse_events():
    def gen():
        q = queue.Queue()
        sse_clients.append(q)
        try:
            yield 'data: {"type":"connected"}\n\n'
            while True:
                try:
                    yield q.get(timeout=30)
                except queue.Empty:
                    continue
        finally:
            if q in sse_clients:
                sse_clients.remove(q)

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ------------------------------------------------------------------------------
# Download worker
# ------------------------------------------------------------------------------
class DownloadWorker(threading.Thread):
    daemon = True

    def run(self):
        while True:
            task = download_queue.get()
            if not task:
                continue

            task_id = task["id"]
            url = task["url"]
            quality = task.get("quality", 3)
            metadata = task.get("metadata", {})

            active_downloads[task_id] = {
                "id": task_id,
                "status": "downloading",
                "metadata": metadata,
            }

            broadcast_sse({
                "type": "download_started",
                "id": task_id,
                "metadata": metadata,
            })

            cmd = ["rip"]
            if os.path.exists(STREAMRIP_CONFIG):
                cmd += ["--config-path", STREAMRIP_CONFIG]

            cmd += ["-f", DOWNLOAD_DIR, "-q", str(quality), "url", url]

            output = []

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

                for line in proc.stdout:
                    output.append(line.rstrip())
                    broadcast_sse({
                        "type": "download_progress",
                        "id": task_id,
                        "line": line.rstrip(),
                    })

                proc.wait()
                status = "completed" if proc.returncode == 0 else "failed"

                broadcast_sse({
                    "type": "download_completed",
                    "id": task_id,
                    "status": status,
                    "output": "\n".join(output),
                    "metadata": metadata,
                })

            except Exception as e:
                broadcast_sse({
                    "type": "download_error",
                    "id": task_id,
                    "error": str(e),
                })

            finally:
                active_downloads.pop(task_id, None)
                download_queue.task_done()


for _ in range(MAX_CONCURRENT_DOWNLOADS):
    DownloadWorker().start()

# ------------------------------------------------------------------------------
# UI
# ------------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

# ------------------------------------------------------------------------------
# Downloads
# ------------------------------------------------------------------------------
@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.json or {}
    url = data.get("url")
    quality = data.get("quality", 3)
    metadata = data.get("metadata", {})

    if not url:
        return jsonify({"error": "URL required"}), 400

    task_id = f"dl_{int(time.time()*1000)}"
    download_queue.put({
        "id": task_id,
        "url": url,
        "quality": quality,
        "metadata": metadata,
    })

    return jsonify({"task_id": task_id, "status": "queued"})


@app.route("/api/download-from-url", methods=["POST"])
def api_download_from_url():
    return api_download()


@app.route("/api/history", methods=["GET"])
def api_history():
    return jsonify(download_history)

# ------------------------------------------------------------------------------
# Delete files / folders
# ------------------------------------------------------------------------------
@app.route("/api/delete-file", methods=["POST"])
def api_delete_file():
    data = request.json or {}
    path = data.get("path")

    if not path:
        return jsonify({"error": "Missing path"}), 400

    full_path = os.path.join(DOWNLOAD_DIR, path)
    if not os.path.exists(full_path):
        return jsonify({"error": "File not found"}), 404

    try:
        os.remove(full_path)

        if os.path.exists(DOWNLOADS_DB):
            os.remove(DOWNLOADS_DB)

        return jsonify({"status": "ok"})

    except Exception as e:
        logger.exception("Failed to delete file")
        return jsonify({"error": str(e)}), 500


@app.route("/api/delete-folder", methods=["POST"])
def api_delete_folder():
    data = request.json or {}
    folder = data.get("path")

    if not folder:
        return jsonify({"error": "Missing folder path"}), 400

    full_path = os.path.join(DOWNLOAD_DIR, folder)
    if not os.path.exists(full_path):
        return jsonify({"error": "Folder not found"}), 404

    try:
        shutil.rmtree(full_path)

        if os.path.exists(DOWNLOADS_DB):
            os.remove(DOWNLOADS_DB)

        return jsonify({"status": "ok"})

    except Exception as e:
        logger.exception("Failed to delete album")
        return jsonify({"error": str(e)}), 500

# ------------------------------------------------------------------------------
# Search
# ------------------------------------------------------------------------------
@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.json or {}
    query = data.get("query")
    source = data.get("source", "qobuz")
    kind = data.get("type", "album")

    if not query:
        return jsonify({"error": "query required"}), 400

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        out = tmp.name

    cmd = ["rip"]
    if os.path.exists(STREAMRIP_CONFIG):
        cmd += ["--config-path", STREAMRIP_CONFIG]

    cmd += ["search", "--output-file", out, source, kind, query]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return jsonify({"error": "search failed"}), 500

    try:
        with open(out) as f:
            items = json.load(f)
    finally:
        os.unlink(out)

    results = []
    for i in items:
        results.append({
            "id": i.get("id"),
            "service": i.get("source", source),
            "type": i.get("media_type", kind),
            "title": i.get("desc"),
            "artist": i.get("artist"),
            "url": f"https://open.qobuz.com/{i.get('media_type')}/{i.get('id')}",
        })

    return jsonify({"results": results})

# ------------------------------------------------------------------------------
# File browser
# ------------------------------------------------------------------------------
@app.route("/api/browse", methods=["GET"])
def api_browse():
    items = []

    if not os.path.exists(DOWNLOAD_DIR):
        return jsonify(items)

    for entry in sorted(os.listdir(DOWNLOAD_DIR)):
        full_path = os.path.join(DOWNLOAD_DIR, entry)

        if os.path.isdir(full_path):
            tracks = []
            for f in sorted(os.listdir(full_path)):
                fp = os.path.join(full_path, f)
                if os.path.isfile(fp):
                    tracks.append({
                        "name": f,
                        "path": os.path.relpath(fp, DOWNLOAD_DIR),
                        "size": os.path.getsize(fp),
                        "modified": os.path.getmtime(fp),
                    })

            items.append({
                "type": "album",
                "name": entry,
                "tracks": tracks,
            })

        elif os.path.isfile(full_path):
            items.append({
                "type": "file",
                "name": entry,
                "path": entry,
                "size": os.path.getsize(full_path),
                "modified": os.path.getmtime(full_path),
            })

    return jsonify(items)

# ------------------------------------------------------------------------------
# Qobuz helpers / quality / artwork
# ------------------------------------------------------------------------------
def get_qobuz_app_id():
    return "798273057"


@app.route("/api/quality", methods=["POST"])
def api_quality():
    data = request.json or {}
    source = data.get("source")
    media_type = data.get("type")
    item_id = data.get("id")

    if source != "qobuz" or not item_id:
        return jsonify({"quality": None})

    try:
        app_id = get_qobuz_app_id()

        if media_type == "track":
            url = "https://www.qobuz.com/api.json/0.2/track/get"
            params = {"track_id": item_id, "app_id": app_id}
        else:
            url = "https://www.qobuz.com/api.json/0.2/album/get"
            params = {"album_id": item_id, "app_id": app_id}

        r = requests.get(url, params=params, timeout=5)
        if r.status_code != 200:
            return jsonify({"quality": None})

        data = r.json()
        quality = {
            "bit_depth": data.get("maximum_bit_depth"),
            "sample_rate": data.get("maximum_sampling_rate"),
            "channels": data.get("maximum_channel_count"),
            "hires": data.get("hires"),
            "label": data.get("maximum_technical_specifications"),
        }

        return jsonify({"quality": quality})

    except Exception:
        logger.exception("quality error")
        return jsonify({"quality": None})


@app.route("/api/album-art")
def api_album_art():
    source = request.args.get("source")
    media_type = request.args.get("type")
    item_id = request.args.get("id")

    if source != "qobuz" or not item_id:
        return jsonify({"album_art": ""})

    try:
        app_id = get_qobuz_app_id()

        if media_type == "track":
            r = requests.get(
                "https://www.qobuz.com/api.json/0.2/track/get",
                params={"track_id": item_id, "app_id": app_id},
                timeout=5,
            )
            image = r.json().get("album", {}).get("image", {}).get("large")
        else:
            r = requests.get(
                "https://www.qobuz.com/api.json/0.2/album/get",
                params={"album_id": item_id, "app_id": app_id},
                timeout=5,
            )
            image = r.json().get("image", {}).get("large")

        return jsonify({"album_art": image or ""})

    except Exception:
        logger.exception("album art error")
        return jsonify({"album_art": ""})

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------
@app.route("/api/config", methods=["GET"])
def api_config():
    if not os.path.exists(STREAMRIP_CONFIG):
        return jsonify({"config": ""})

    with open(STREAMRIP_CONFIG) as f:
        return jsonify({"config": f.read()})


# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
