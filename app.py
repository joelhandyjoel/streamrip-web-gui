import logging
import os
import re
import json
import time
import queue
import shutil
import tempfile
import subprocess
import threading
import requests

from flask import Flask, render_template, request, jsonify, Response, stream_with_context

print("🔥🔥🔥 LOADED THIS APP.PY 🔥🔥🔥")


# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# App setup
# ------------------------------------------------------------------------------
app = Flask(__name__)

STREAMRIP_CONFIG = os.environ.get(
    "STREAMRIP_CONFIG",
    "/config/streamrip/config.toml"
)
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/music")
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "2"))

download_queue = queue.Queue()
active_downloads = {}
download_history = []
sse_clients = []








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
            yield "data: {\"type\":\"connected\"}\n\n"
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

            active_downloads[task_id] = {"status": "downloading", "metadata": metadata}
            broadcast_sse({"type": "download_started", "id": task_id, "metadata": metadata})

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
                    if len(output) % 10 == 0:
                        broadcast_sse({
                            "type": "download_progress",
                            "id": task_id,
                            "output": "\n".join(output[-5:]),
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
                download_history.append({
                    "id": task_id,
                    "metadata": metadata,
                    "output": "\n".join(output),
                })
                download_queue.task_done()

# Start workers
for _ in range(MAX_CONCURRENT_DOWNLOADS):
    DownloadWorker().start()

# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.json or {}
    url = data.get("url")
    quality = data.get("quality", 3)

    if not url:
        return jsonify({"error": "URL required"}), 400

    task_id = f"dl_{int(time.time()*1000)}"
    download_queue.put({
        "id": task_id,
        "url": url,
        "quality": quality,
        "metadata": {},
    })

    return jsonify({"task_id": task_id, "status": "queued"})

@app.route("/api/download-from-url", methods=["POST"])
def api_download_from_url():
    # Compatibility shim for frontend
    return api_download()


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
        return jsonify({"error": "search failed", "stdout": result.stdout}), 500

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
            "url": construct_url(i.get("source", source), i.get("media_type", kind), i.get("id")),
        })

    return jsonify({"results": results})


def get_qobuz_app_id():
    """
    Return a valid Qobuz app_id.
    Streamrip uses 798273057 and it works without auth.
    """
    return "798273057"


# ------------------------------------------------------------------------------
# QUALITY ENDPOINT (STABLE)
# ------------------------------------------------------------------------------
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
        elif media_type == "album":
            url = "https://www.qobuz.com/api.json/0.2/album/get"
            params = {"album_id": item_id, "app_id": app_id}
        else:
            return jsonify({"quality": None})

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




# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def parse_qobuz_quality(output: str):
    """
    Parse streamrip stdout for Qobuz quality info
    """
    matches = re.findall(r"format_id=(\d+)", output)
    if not matches:
        return None

    ids = sorted(set(int(x) for x in matches))
    best = max(ids)

    labels = {
        5: "MP3 320",
        6: "FLAC 16-bit / 44.1 kHz",
        7: "FLAC 24-bit",
        27: "FLAC 24-bit Hi-Res",
    }

    return {
        "best_format_id": best,
        "label": labels.get(best, "Unknown"),
        "all_format_ids": ids,
    }

def construct_url(source, media_type, item_id):
    if not item_id:
        return ""
    if source == "qobuz":
        return f"https://open.qobuz.com/{media_type}/{item_id}"
    return ""


@app.route("/api/album-art")
def api_album_art():
    source = request.args.get("source")
    media_type = request.args.get("type")
    item_id = request.args.get("id")

    if source != "qobuz" or not item_id:
        return jsonify({"album_art": ""})

    try:
        app_id = "798273057"  # hardcode for now (stable)

        if media_type == "track":
            r = requests.get(
                "https://www.qobuz.com/api.json/0.2/track/get",
                params={"track_id": item_id, "app_id": app_id},
                timeout=5,
            )
        elif media_type == "album":
            r = requests.get(
                "https://www.qobuz.com/api.json/0.2/album/get",
                params={"album_id": item_id, "app_id": app_id},
                timeout=5,
            )
        else:
            return jsonify({"album_art": ""})

        if r.status_code != 200:
            return jsonify({"album_art": ""})

        data = r.json()

        # 🔑 THIS IS THE IMPORTANT PART
        image = None
        if media_type == "track":
            image = (
                data
                .get("album", {})
                .get("image", {})
                .get("large")
            )
        else:
            image = (
                data
                .get("image", {})
                .get("large")
            )

        return jsonify({"album_art": image or ""})

    except Exception:
        logger.exception("album art error")
        return jsonify({"album_art": ""})

@app.route("/api/config", methods=["GET"])
def api_config():
    try:
        if not os.path.exists(STREAMRIP_CONFIG):
            return jsonify({"config": "", "path": STREAMRIP_CONFIG})

        with open(STREAMRIP_CONFIG, "r") as f:
            return jsonify({
                "config": f.read(),
                "path": STREAMRIP_CONFIG
            })
    except Exception:
        logger.exception("config error")
        return jsonify({"config": "", "path": STREAMRIP_CONFIG})

@app.route("/api/files", methods=["GET"])
def api_files():
    files = []

    for root, _, filenames in os.walk(DOWNLOAD_DIR):
        for name in filenames:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, DOWNLOAD_DIR)

            files.append({
                "name": rel,
                "size": os.path.getsize(path),
                "mtime": os.path.getmtime(path),
            })

    files.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify({"files": files})



# ------------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Streamrip Web")
    app.run(host="0.0.0.0", port=5000)
