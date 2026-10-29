# kong-sre-lab

This is a local Kubernetes lab I built in October 2026 while studying for SRE and cloud interviews. It's a small Flask app backed by Redis, deployed with a Helm chart I wrote, running in dev, stage and prod namespaces on a single-node kind cluster, with Kong in front of it as the ingress controller.

I wrote the files and ran the commands myself, and used an AI tutor to explain things and review my work as I went. It's a lab, so it's one node, local images, no TLS and no real traffic.

## What's in it

- The app has two health endpoints. `/healthz` is the liveness check and only confirms the process is up. `/readyz` is the readiness check and pings Redis, returning a 503 if it can't reach it. That way Kubernetes pulls the pod out of the Service when Redis is down instead of restarting it.
- The Helm chart has a Deployment, Service, ConfigMap and a Redis instance per release. I set resource requests and limits, liveness and readiness probes, and a rolling update with `maxUnavailable: 0` and `maxSurge: 1`.
- dev, stage and prod all use the same chart with a small values file each. The main difference is the replica count (1, 2 and 3).
- I did a rolling update from v1 to v2, rolled it back with `helm rollback`, and checked which image the Deployment was running after each step.
- Kong runs DB-less and routes `/dev`, `/stage` and `/prod` to each namespace. Prod also has key-auth and a rate limit of 5 requests per minute per consumer, with the counter stored in Redis.
- The Dockerfile pins dependencies, copies `requirements.txt` before the code so the pip layer stays cached, runs as a non-root user, has a health check, and takes the app version as a build arg.

## Layout

```
app.py                Flask app: /healthz, /readyz, /api (Redis hit counter)
Dockerfile            image build
requirements.txt      pinned dependencies
kind-config.yaml      one-node cluster, maps localhost:8000 to Kong's NodePort 30080
kong-values.yaml      Helm values for Kong (NodePort proxy, TLS off)
chart/kong-lab-api/   the Helm chart and the dev/stage/prod values files
k8s/kong/             an Ingress per environment, plus key-auth, consumer and rate limit for prod
```

## Running it

You need Docker, kind, kubectl and Helm. I pieced these steps back together from my notes, so treat them as a guide rather than a tested script.

```bash
# cluster
kind create cluster --config kind-config.yaml

# kind can't pull local images, so build them and load them into the node
docker build -t kong-lab-api:v1 --build-arg APP_VERSION=v1 .
docker build -t kong-lab-api:v4 --build-arg APP_VERSION=v4 .
kind load docker-image kong-lab-api:v1 kong-lab-api:v4 --name kong-lab

# the app, installed as a release called "api" in each namespace
for env in dev stage prod; do
  helm install api chart/kong-lab-api -n "$env" --create-namespace \
    -f "chart/kong-lab-api/values-$env.yaml"
done

# Kong
helm repo add kong https://charts.konghq.com && helm repo update
helm install kong kong/ingress -n kong --create-namespace -f kong-values.yaml

# routes and the prod plugins
kubectl apply -f k8s/kong/
```

The API key for prod isn't in the repo on purpose. I created it with kubectl:

```bash
kubectl create secret generic brian-key -n prod --from-literal=key=<your-key>
kubectl label secret brian-key -n prod konghq.com/credential=key-auth
```

Then to test:

```bash
curl -i localhost:8000/dev/api                            # 200 with the hit count and version
curl -i localhost:8000/prod/api                           # 401, no key
curl -i -H 'apikey: <your-key>' localhost:8000/prod/api   # 200, then 429 after 5 in a minute
```

## Breaking it on purpose

I caused each of these errors deliberately and worked out where it came from by reading the response and headers.

| Code | What I did | Where it came from |
|---|---|---|
| 404 | hit a path with no route | Kong ("no Route matched", no upstream latency) |
| 404 | route matched but Kong passed the full path to Flask | the app (`Server: Werkzeug`, `Via: kong`), fixed with `konghq.com/strip-path` |
| 401 | no key, then a wrong key, on prod | Kong key-auth ("No API key found" / "Unauthorized"). The hit counter didn't move, so the requests never reached the app |
| 429 | more than 5 requests in a minute | Kong rate limiting (`RateLimit-*` and `Retry-After` headers) |
| 503 | scaled Redis to 0 | first the app ("dependency unavailable"), then Kong ("failure to get a peer from the ring-balancer") once readiness pulled the pod. The pod stayed Running and didn't restart |

You can also add latency or random errors with `EXTRA_LATENCY_MS` and `ERROR_RATE` in the chart's `app` values.

## Problems I ran into

**Slow failures during a Redis outage.** With Redis down, one request took up to 25 seconds even though I'd set a 1 second timeout. When I timed the connect attempts, it turned out redis-py has its own 5 second connect timeout, separate from `socket_timeout`, and retries 10 times with backoff. My timeout never kicked in because a connection was never made. In v3 I set `socket_connect_timeout` and capped retries at 1, both configurable through env vars, and failed requests went down to about a second or less.

**Pods taking 32 seconds to delete.** They also ended in `Error`. The app is PID 1 in the container, and `/proc/1/status` showed it had no SIGTERM handler, so it ignored SIGTERM until Kubernetes killed it at the end of the grace period. In v4 I added a handler that logs and exits, and deletes dropped to about a second. It still doesn't drain in-flight requests. For that I'd need something like gunicorn and probably a `preStop` hook.

dev is on v4. stage and prod are still on v1 from the rollback test.

## Not done yet

Multi-node or managed clusters (EKS and so on), TLS, proper secrets management, Prometheus and Grafana, Argo CD and tracing are all things I'd like to add later. None of them are in here now.

My earlier Docker, Bash and GitHub Actions lab is [sre-lab](https://github.com/workbleecloud/sre-lab).
