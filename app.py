import os
import time
import random

import redis
from flask import Flask, jsonify

app = Flask(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
APP_VERSION = os.environ.get("APP_VERSION", "v1")
EXTRA_LATENCY_MS = int(os.environ.get("EXTRA_LATENCY_MS", "0"))
ERROR_RATE = float(os.environ.get("ERROR_RATE", "0"))

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=1)

@app.route("/healthz")
def healthz():
    # Liveness: "is this process alive?" Never check dependencies here.
    return jsonify(status="ok", version=APP_VERSION)

@app.route("/readyz")
def readyz():
    # Readiness: "can I serve traffic right now?" Check dependencies here.
    try:
        r.ping()
        return jsonify(status="ready", version=APP_VERSION)
    except redis.exceptions.RedisError as e:
        return jsonify(status="not ready", error=str(e)), 503

@app.route("/api")
def api():
    if EXTRA_LATENCY_MS:
        time.sleep(EXTRA_LATENCY_MS / 1000)
    if random.random() < ERROR_RATE:
        return jsonify(error="injection failure"), 500
    try:
        count = r.incr("hits")
    except redis.exceptions.RedisError as e:
        app.logger.error("redis unavailable: %s", e)
        return jsonify(error="dependency unavailable"), 503
    return jsonify(hits=count, version=APP_VERSION)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)