import os
import time
import random
import redis
import signal
import sys
from flask import Flask, jsonify
from redis.backoff import ExponentialWithJitterBackoff
from redis.retry import Retry

app = Flask(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
APP_VERSION = os.environ.get("APP_VERSION", "v1")
EXTRA_LATENCY_MS = int(os.environ.get("EXTRA_LATENCY_MS", "0"))
ERROR_RATE = float(os.environ.get("ERROR_RATE", "0"))
REDIS_TIMEOUT = float(os.getenv("REDIS_TIMEOUT", "1"))
REDIS_RETRIES = int(os.getenv("REDIS_RETRIES", "1"))

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    socket_timeout=REDIS_TIMEOUT,          # send/read phase
    socket_connect_timeout=REDIS_TIMEOUT,  # connect phase (library default is 5 s)
    retry=Retry(ExponentialWithJitterBackoff(base=0.01, cap=0.1), REDIS_RETRIES),
)

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

def handle_sigterm(signum, frame):
    # PID 1 ignores SIGTERM unless a handler is installed; exit promptly instead
    # of waiting for SIGKILL. This does not drain in-flight requests.
    app.logger.warning("SIGTERM received, shutting down")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)