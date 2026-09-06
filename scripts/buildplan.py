#!/usr/bin/env python3
"""Build the article plan: curate, schedule, label.

Entries are (slug, title, labels, source, problem). Order within each maturity
bucket is the publication order: foundations before the things built on them.
"""
from datetime import date, timedelta
import json, pathlib

# ── EARLY: Linux, Ansible, Docker, Python foundations ────────────────────────
EARLY = [
 ("ansible-ssh-port-migration","Migrating the SSH Port Mid-Playbook Without Locking Ansible Out",["ansible","ssh","linux","idempotence"],"iac","Changing sshd's port during provisioning severs the control connection mid-run unless the config check, restart, and connection update are sequenced correctly."),
 ("ansible-role-idempotence","Ansible Role Idempotence: Why a changed=0 Run Is the Only Proof You Have",["ansible","idempotence","infrastructure-as-code","testing"],"iac","A playbook that reports changes on every run has stopped being a drift detector, and nobody notices because it still exits zero."),
 ("docker-compose-convergence-ansible","Deploying Docker Compose from Ansible Without Shelling Out",["ansible","docker","docker-compose","idempotence"],"iac","Wrapping `docker compose up` in a shell task with changed_when:false can never report a real change or work in check mode."),
 ("sysctl-reasserted-after-docker","Reasserting Kernel Sysctls That Docker Silently Reverts",["linux","docker","systemd","hardening"],"iac","Docker rewrites several net.ipv4 sysctls when it creates bridges, undoing boot-time hardening with no error and no log line."),
 ("docker-registry-credentials-two-users","Docker Registry Credentials for Two Local Users on One Host",["docker","ansible","linux","permissions"],"iac","Docker reads the config of whoever invokes the CLI, so writing only root's credential leaves the deploy user unauthenticated and failing silently."),
 ("linux-file-permissions-setgid","chmod -R and the setgid Bit: Why a Four-Digit Mode Is Not Enough",["linux","permissions","containers","testing"],"k8s","A four-digit chmod does not clear setgid, so a directory keeps propagating group ownership after an apparently correct fix."),
 ("fastapi-project-layout","A Feature-Module Layout for a FastAPI Application That Keeps Growing",["fastapi","python","architecture","project-structure"],"taberna","A single app collapses into one giant routers.py where nobody can add a feature without touching code they do not own."),
 ("fastapi-response-envelope","Designing a Consistent FastAPI Response Envelope",["fastapi","pydantic","api-design","python"],"taberna","Ad-hoc return values leave every client guessing whether a payload is wrapped and every error a different shape."),
 ("fastapi-exception-hierarchy","An Exception Hierarchy and One Handler for FastAPI Error Responses",["fastapi","python","error-handling","api-design"],"taberna","Bare HTTPExceptions scattered across a codebase produce inconsistent payloads and lose the context needed to debug them."),
 ("pydantic-closed-vocabulary","Closed-Vocabulary Pydantic Validation for a Public Ingest Endpoint",["fastapi","pydantic","validation","api-design"],"taberna","An endpoint anyone can POST to will accept a typo'd field and silently drop it, hiding a client bug indefinitely."),
 ("layered-configuration-precedence","Configuration Precedence: Docker Secrets, Kubernetes Secrets, Environment, .env, Default",["python","configuration","docker","kubernetes"],"taberna","A service reading only environment variables forces every deployment target to shoehorn secrets into one mechanism, and nothing says which value won."),
 ("uv-ruff-python-tooling","Replacing pip, venv, flake8, black and isort with uv and Ruff",["python","uv","ruff","tooling"],"taberna","requirements.txt gives no lockfile guarantee, and four separate lint tools triple the config surface for a small team."),
 ("python-cli-scaffolding","A Python CLI That Scaffolds a Project Structure You Actually Want",["python","cli","developer-tools","scaffolding"],"taberna","Starting a new service means recreating the same layout, Dockerfile and ignore rules by hand, or copy-pasting from the last one."),
 ("edge-node-provisioning","Provisioning a Single-Board Computer as an Industrial Edge Node",["linux","edge-compute","provisioning","systemd"],"horus","Turning a bare carrier board into a working node is a manual sequence that differs subtly between board revisions."),
 ("poe-multicast-discovery","Powering and Discovering Peripherals over PoE with Multicast Announcements",["networking","industrial-iot","multicast","hardware"],"horus","Separate power cabling to each sensor multiplies installation cost, and every new module otherwise needs manual addressing."),
 ("dns-read-merge-write","Managing a Shared DNS Zone Through a Replace-Everything API",["dns","automation","idempotence","api-design"],"iac","Registrar APIs often expose only 'replace the whole zone', so naive automation deletes every record it does not manage."),
 ("graceful-shutdown-go","A Minimal Go HTTP Service with chi, Timeouts and Graceful Shutdown",["go","http","backend","reliability"],"taberna","ListenAndServe drops in-flight requests on SIGTERM and gives you nowhere obvious to put middleware or timeouts."),
 ("docker-multistage-images","Multi-Stage Docker Builds: Shipping a Runtime Without the Toolchain",["docker","containers","build-performance"],"horus","Building in the same image you ship means every runtime container carries a compiler it will never use."),
]

# ── MIDDLE: Ceph, Vault, Redis, C++, benchmarking, OIDC, payments ────────────
MIDDLE = [
 ("ceph-layered-benchmark","A Layered Storage Benchmark: Isolating Device, Network and Protocol Bottlenecks",["ceph","storage","benchmarking","performance"],"iac","One throughput number hides whether the bottleneck is a slow disk, a saturated link, or protocol contention."),
 ("smr-drives-in-clusters","SMR Drives in a Replicated Cluster: Finding the Disk That Ruins the Pool",["ceph","storage","hardware","linux"],"iac","Drive-managed SMR disks look identical in inventory and collapse under sustained random writes, dragging the whole pool down."),
 ("ceph-crush-hybrid-tiering","CRUSH Hybrid Rules: Pinning the Read Primary to SSD Without Rebalancing",["ceph","storage","performance","crush"],"iac","Metadata-heavy reads suffer on spinning disks, but moving the whole pool to flash triggers a rebalance there is no room for."),
 ("ceph-full-osd-threshold","Capacity Planning Against the Fullest OSD, Not the Average",["ceph","storage","capacity-planning","reliability"],"iac","Placement is pseudo-random, so a cluster that looks 70% full can have a disk at 90% — and one full OSD stops cluster-wide writes."),
 ("ceph-recovery-throttling","Tuning Ceph Recovery: The Trade Between Degraded Time and Client Latency",["ceph","storage","performance","reliability"],"iac","Recovery I/O arrives exactly when you have less hardware than usual; too aggressive looks like an outage, too gentle risks the second failure."),
 ("vault-ssh-certificate-authority","Replacing Distributed SSH Keys with a Vault Certificate Authority",["vault","ssh","pki","security"],"iac","Distributing and rotating static public keys across a fleet does not scale and leaves no audit trail of who accessed what."),
 ("vault-kv2-patch-vs-put","Vault KV-v2: Why put Silently Wipes Every Sibling Field",["vault","secrets-management","security"],"iac","put replaces the whole secret, so adding one field destroys the others — and there is no single-key delete."),
 ("vault-agent-sidecar","Runtime Secret Injection with a Vault Agent Sidecar and a Wrapped AppRole",["vault","secrets-management","security","docker"],"iac","Rendering secrets into config files at deploy time leaves plaintext on disk and makes rotation a redeploy."),
 ("vault-auto-unseal-tang","Auto-Unsealing Vault Without Cloud KMS or a TPM, Using Tang and Clevis",["vault","encryption","linux","security"],"iac","Manual unsealing does not scale once services depend on the secret store at boot, but cloud KMS means trusting a third party."),
 ("redis-sorted-set-conveyor","Tracking Moving Objects with Redis Sorted Sets and an Atomic Lua Tick",["redis","event-sourcing","lua","data-modeling"],"horus","Tracking several objects entering and leaving a line needs 'where is everything now' and 'drop what left' without a database."),
 ("redis-pipeline-decomposition","Chaining Single-Purpose Redis Consumers into a Processing Pipeline",["redis","architecture","python","pipelines"],"horus","A monolithic 'process all the data' service becomes untestable once it does detection, tracking, calibration and statistics at once."),
 ("redis-single-writer-config","A Single-Writer Rule for Configuration Held in Redis",["redis","configuration","distributed-systems","consistency"],"horus","When several services write the same key, a read after a write can return another writer's value and nobody owns the truth."),
 ("redis-maxmemory-stream-trim","Sizing Redis maxmemory Against a Stream Trim Threshold",["redis","capacity-planning","databases","reliability"],"k8s","Trimming only runs on write, so hitting the memory cap first means the stream can never shrink and every write is refused."),
 ("uuid-normalization","UUID Case Normalization as a Recurring Cross-Service Bug",["data-integrity","distributed-systems","python","testing"],"horus","An uppercase UUID from one service and a lowercase one from another create two keys instead of one, silently."),
 ("sensor-calibration-ema","Per-Channel Polynomial Calibration and EMA Smoothing in One Pipeline Stage",["signal-processing","sensors","python","redis"],"horus","Recalculating calibration in every consumer duplicates both the logic and the coefficients."),
 ("cpk-as-a-stream","Computing Cpk Continuously Instead of as a Batch Report",["statistics","process-monitoring","redis","python"],"horus","Process capability is traditionally an offline report, but an operator watching a live line needs a current value per zone."),
 ("cpp-redis-consumer-migration","Migrating Redis Consumers from Python to C++ on Constrained Hardware",["cpp","python","redis","performance"],"horus","A dozen Python consumers running continuously become the CPU and memory bottleneck on an edge device."),
 ("cpp-base-image-dependencies","A Shared Multi-Stage Base Image for a C++ Service Fleet",["cpp","docker","cmake","build-performance"],"horus","A dozen services each rebuilding the same HTTP framework and JSON library turn a one-line change into a multi-minute build."),
 ("vcpkg-cross-compilation","Cross-Compiling a vcpkg C++ Project to ARM64",["cpp","vcpkg","cmake","cross-compilation"],"horus","A dependency's build step tries to run a host binary that does not exist for the target, and the fix is an environment variable."),
 ("cpp-websocket-fanout","Fanning Redis Pub/Sub Out to Many WebSocket Clients in C++",["cpp","websocket","redis","real-time"],"horus","Every browser tab holding its own Redis subscription does not scale and exposes the store to untrusted clients."),
 ("mqtt-persistent-sessions","MQTT Persistent Sessions and Retained Messages for Offline Subscribers",["mqtt","messaging","reliability","iot"],"k8s","A message published while a subscriber is offline is lost under clean-session defaults, and late subscribers see no current state."),
 ("mqtt-acls","Per-Account MQTT ACLs as the Real Authorization Boundary",["mqtt","authorization","security","iot"],"k8s","A shared broker serving many flows needs each account restricted, or one bug reads or spoofs another flow's messages."),
 ("order-state-machine","Modelling an Order Lifecycle as an Explicit State Machine",["python","state-machine","domain-modeling","api-design"],"taberna","Letting any code path assign order.status means invalid transitions are caught only by whoever remembers to check."),
 ("stripe-webhook-idempotency","Idempotent Payment Webhooks with a Deduplication Table",["python","idempotency","webhooks","payments"],"taberna","Providers deliver at-least-once and retry on any non-2xx, so a naive handler double-processes payments on every redelivery."),
 ("stock-reservation-check-constraint","Preventing Overselling with a Database CHECK Constraint",["postgresql","concurrency","databases","sqlalchemy"],"taberna","Two customers buying the last unit at the same moment is a race that check-then-decrement code cannot reliably win."),
 ("sql-timezone-day-bucketing","Timezone-Correct Day Bucketing for Revenue Reporting",["postgresql","sql","analytics","data-modeling"],"taberna","Grouping by day on UTC timestamps quietly moves evening orders into the next day for any shop east of Greenwich."),
 ("analytics-in-sql-not-client","Computing Analytics in SQL Instead of From the Last Page of Results",["postgresql","sql","analytics","fastapi"],"taberna","Deriving revenue in the frontend from the last N orders gives numbers that stop being true once volume passes the page size."),
 ("adapter-interfaces-providers","Adapter Interfaces for Swappable Payment, Carrier and Mail Providers",["python","design-patterns","testing","api-design"],"taberna","Calling a vendor SDK from business logic means a provider swap is a rewrite of every call site, not a new class."),
 ("influxdb-attendance-events","Storing Time-Attendance Events in InfluxDB and Rolling Them Up on Read",["influxdb","time-series","fastapi","dashboards"],"ygg","Clock-in events are time-series data, and modelling them relationally makes 'hours this week' slow and verbose."),
 ("bff-normalising-upstreams","One Dashboard on Five Unrelated Backends: Normalising at the Boundary",["fastapi","architecture","api-integration","dashboards"],"ygg","A portal surfacing tickets, time tracking and CI status becomes an unmaintainable ad-hoc proxy without a common shape."),
 ("upstream-200-for-bad-credentials","When an Upstream Answers 200 for Wrong Credentials",["api-integration","authentication","reliability","fastapi"],"ygg","Some registry APIs return 200 and an empty catalog whether you are unauthenticated or simply wrong, so integration reports 'connected, nothing here'."),
 ("gpu-vram-budget-quantisation","Sizing One GPU for Local LLM Inference: VRAM, Quantisation and KV Cache",["llm","gpu","quantization","performance"],"atlas","Weights plus a long context and KV cache must fit fixed VRAM, and the wrong cache format silently triples latency."),
 ("llama-swap-model-swapping","Serving Several Local Models on One GPU with On-Demand Loading",["llm","self-hosting","gpu","model-management"],"atlas","One GPU holds one large model, but different tasks want different models and manual restarts are impractical."),
 ("openai-compatible-proxy","An OpenAI-Compatible Proxy in Front of a Local Model Server",["llm","fastapi","proxy","authentication"],"atlas","Every tool expects the OpenAI shape, but a local server needs auth and a health check that never triggers a model load."),
 ("benchmark-determinism","Making a Benchmark Deterministic Against a Server Tuned for Interactive Use",["benchmarking","llm","testing","reproducibility"],"atlas","Every run scores differently, so a real regression cannot be told apart from sampling noise."),
 ("benchmark-your-own-tasks","Why a Leaderboard Score Does Not Predict Your Workload",["benchmarking","llm","evaluation","testing"],"atlas","A public ranking says nothing about whether a model calls your tools correctly or survives your context lengths."),
 ("tool-call-ast-scoring","Scoring Tool Calls by Parsed Structure Instead of String Equality",["llm","tool-calling","testing","evaluation"],"atlas","String comparison fails on equivalent arguments and passes on wrong calls that happen to contain the right substring."),
 ("power-metering-self-hosting","Metering GPU and CPU Power to a Currency Figure",["observability","self-hosting","cost-analysis","python"],"atlas","The case for self-hosting is unclear without a measured cost per session, and most setups have no wall-power sensor."),
 ("dry-run-first-design","Building the Dry-Run Path First, and Testing That It Sends Nothing",["testing","cli-design","automation","infrastructure-as-code"],"atlas","A dry-run flag bolted on afterwards drifts out of sync with the real code path exactly when you need to trust it."),
 ("oidc-confidential-introspection-client","A Confidential OIDC Client That Exists Only to Introspect Tokens",["oidc","keycloak","authentication","security"],"k8s","An API validating a token minted for a public SPA client cannot use that client to call introspection, which itself needs authentication."),
 ("oidc-introspection-error-shapes","A 500 That Was a Misconfigured Client, Not an Auth Failure",["oidc","error-handling","authentication","testing"],"k8s","Assuming introspection always returns an `active` field turns a client misconfiguration into an unhandled error on every request."),
 ("trusting-two-cas","Trusting an Internal and a Public Certificate Authority in One Process",["tls","pki","security","certificates"],"k8s","Configuring trust for an internal CA cuts the same process off from the public chain it needs to reach an external identity provider."),
 ("buildkit-remote-driver","Running BuildKit as a Remote Builder Without a Docker Daemon",["buildkit","docker","ci-cd","containerd"],"k8s","A CI worker under containerd has no docker.sock, so buildx's default driver cannot start BuildKit at all."),
 ("buildkit-rootless-tradeoff","Rootless BuildKit: What fuse-overlayfs Costs on a Cold Cache",["buildkit","security","containers","performance"],"k8s","The standard BuildKit image needs a privileged context, which is unattractive on a cluster watching for privileged workloads."),
 ("dual-arch-ci-pipelines","Building One Tag Natively on Two CPU Architectures",["ci-cd","docker","arm64","github-actions"],"horus","An amd64 runner building arm64 under emulation can be an order of magnitude slower, which matters for a fleet of edge devices."),
 ("go-embed-spa","Embedding a Built Frontend Into a Go Binary with go:embed",["go","spa","deployment","architecture"],"k8s","Running a static file server and an API as two processes is more moving parts than the app needs, and they can version-skew."),
 ("docker-build-api-streaming","Designing a Docker Image Build API: Job Model and Streamed Logs",["go","docker","api-design","streaming"],"taberna","A build is not request/response, and a client needs to watch output live rather than poll for a final status."),
 ("angular-layering","Layering an Angular Application: core, shared, features and the Rule That Holds",["angular","typescript","frontend","architecture"],"taberna","Without enforced layering, API calls end up inside presentational components and every screen becomes its own client."),
 ("angular-runtime-config","Runtime Configuration for an Angular Container Without Rebuilding",["angular","docker","configuration","nginx"],"taberna","Baking a backend URL into the build means a different deployment target needs a full rebuild instead of a variable."),
 ("design-tokens-discipline","Design Tokens Over Literal Colours in Templates",["frontend","design-systems","angular","css"],"taberna","Hex values scattered across templates make a UI drift screen by screen and 'what does this colour mean' stops having an answer."),
 ("angular-ssr-behind-proxy","Server-Side Rendering Angular Behind a Reverse Proxy",["angular","ssr","nginx","docker"],"ygg","SSR turns the frontend into a long-running process that must be reachable from the proxy, unlike a static bundle."),
 ("pdf-report-generation","Generating a Print-Ready PDF Report from JSON Without a Browser",["python","fastapi","reporting","pdf"],"horus","Operators need a printable record with a chart and thresholds, and a headless browser is a heavy dependency for it."),
 ("sensor-data-simulation","Simulating Sensor Data So the Pipeline Can Be Tested Without Hardware",["testing","simulation","redis","sensors"],"horus","Testing an event pipeline end to end normally needs physical hardware on a bench, which cannot run in CI."),
 ("multi-service-compose-dev","A Multi-Service docker-compose That a New Contributor Can Actually Start",["docker-compose","local-development","keycloak","testing"],"ygg","A backend needing an identity provider, a database, a cache and a time-series store is hard to stand up reproducibly."),
 ("alembic-at-entrypoint","Running Alembic Migrations Once, Before the Workers Fork",["alembic","migrations","docker","postgresql"],"taberna","Migrating from application code that starts N workers risks running the migration N times, or serving requests mid-migration."),
 ("pure-function-domain-logic","Testing Domain Calculations as Pure Functions Without a Database",["python","testing","domain-modeling"],"taberna","Business calculations written as methods on a database model need a session and fixtures to test what is really arithmetic."),
 ("transactional-outbox","The Transactional Outbox: Never Losing a Job You Already Committed",["python","distributed-systems","reliability","sqlalchemy"],"taberna","Enqueueing after commit leaves a gap where the state change is permanent but the job never runs and nothing retries."),
]

# ── LATE: Kubernetes, GitOps, mesh, identity, observability, agents ──────────
LATE = [
 ("argocd-app-of-apps","Argo CD App-of-Apps: Bootstrapping a Bare Cluster from One Root Application",["kubernetes","argocd","gitops","automation"],"k8s","A fresh cluster has nothing installed, and hand-applying manifests in the right order does not survive a rebuild."),
 ("argocd-applicationsets","ApplicationSets: Onboarding a Workload With a Directory and a Pull Request",["kubernetes","argocd","gitops","automation"],"k8s","Registering each new service with its own Application object is repetitive and easy to forget."),
 ("argocd-sync-waves","Sync Waves and the CRD-Before-Consumer Ordering Problem",["kubernetes","argocd","crd","testing"],"k8s","Deploying a CRD and a resource using it in the same sync races, and pruning the CRD owner can delete every object built on it."),
 ("gitops-image-tag-pinning","Pinning Image Tags to Git SHAs Because GitOps Diffs Manifests, Not Registries",["gitops","ci-cd","docker","argocd"],"k8s","With a floating tag, CI can push a new image and nothing in the manifest changes, so no rollout happens."),
 ("gitops-bootstrap-circularity","Breaking the Circular Dependency in GitOps Secret Delivery",["kubernetes","gitops","argocd","secrets-management"],"iac","A secrets operator deployed by the GitOps controller cannot also deliver that controller's own login secret."),
 ("ceph-csi-storageclasses","Ceph CSI StorageClasses: RBD for Block, CephFS for Shared Volumes",["kubernetes","ceph","storage","csi"],"k8s","Databases need exclusive block devices and multi-pod workloads need shared volumes, from one storage cluster."),
 ("static-persistent-volumes","Importing Pre-Existing Storage as a Static PersistentVolume",["kubernetes","storage","ceph","reliability"],"k8s","Data that already exists outside Kubernetes must be mountable without being provisioned, and must survive a mistaken delete."),
 ("object-storage-durability-boundary","Object Storage as the Durability Boundary for a Container Registry",["kubernetes","s3","object-storage","container-registry"],"k8s","Blobs on a cluster-managed volume are one reclaim-policy or prune mistake away from deletion."),
 ("registry-blob-redirect","Registry Blob Redirects That Point Somewhere the Client Cannot Reach",["container-registry","s3","networking","docker"],"k8s","An S3-backed registry can redirect a download to an internal endpoint that external clients cannot resolve."),
 ("fsgroup-recursive-chown","fsGroup Recursive chown Hangs on Large Volumes",["kubernetes","storage","cephfs","performance"],"k8s","Kubernetes chowns every file at mount time, which can hang pod startup for hours on a volume with millions of files."),
 ("init-container-ordering","An Init Container That Cannot Fix What It Runs Before",["kubernetes","reliability","containers","testing"],"k8s","A repair container that runs once at pod creation cannot re-run when the main container crashloops, and can succeed vacuously."),
 ("binfmt-daemonset","A DaemonSet That Registers a Kernel Feature and Then Does Nothing",["kubernetes","multi-arch","qemu","containers"],"k8s","Cross-architecture builds need binfmt handlers registered as host kernel state, which is lost on every reboot."),
 ("statefulset-immutable-resize","Resizing a Volume Behind an Immutable StatefulSet Template",["kubernetes","storage","statefulset","patterns"],"k8s","volumeClaimTemplates storage size is immutable, so growing a PVC leaves the manifest permanently disagreeing with reality."),
 ("cronjob-not-job-for-sweeps","Why a Sweep Belongs in a CronJob and Not a Job",["kubernetes","automation","argocd","reliability"],"k8s","A failed one-shot Job is terminally unhealthy and blocks reconciliation of the whole application until someone deletes it."),
 ("cpu-feature-scheduling","CPU Instruction Sets as a Scheduling Constraint",["kubernetes","hardware","helm","reliability"],"k8s","A chart's default dependency can crash with an illegal instruction on older CPUs, looking like a random crash rather than an incompatibility."),
 ("k8s-dns-canary","A DNS Canary CronJob as a Regression Test for Split-Horizon Resolution",["kubernetes","dns","monitoring","testing"],"k8s","Pods can inherit a search domain that resolves public names to an internal blackhole, and the regression can silently reappear."),
 ("kubelet-ndots-search-leak","ndots and the Accidental Search-Domain Leak in Pod DNS",["kubernetes","dns","networking","kubelet"],"k8s","A pod's search list can append an internal domain to every external lookup, resolving it to nothing useful or worse."),
 ("cni-mtu-inheritance","How a VPN Client Silently Shrinks Your Pod Network MTU",["kubernetes","networking","cni","mtu"],"iac","A CNI auto-detecting MTU from the lowest interface drops the pod network to fragmented packets with no error, just worse throughput."),
 ("cert-manager-internal-ca","cert-manager for Mesh-Only Names That No ACME Challenge Can Reach",["kubernetes","cert-manager","tls","pki"],"k8s","Internal services need TLS but have no public record for an HTTP-01 or DNS-01 challenge to validate against."),
 ("eso-vault-jwt-offline","External Secrets with Offline JWT Validation When Vault Cannot Reach the Cluster",["kubernetes","vault","external-secrets","security"],"k8s","The standard Kubernetes auth method needs a callback into the API server, which fails when the two are isolated by design."),
 ("eso-envfrom-fanout","One ExternalSecret, Many Environment Variables",["kubernetes","external-secrets","secrets-management","vault"],"k8s","Wiring a dozen values through repeated secretKeyRef entries is verbose and drifts between Deployments."),
 ("secret-rotation-blast-radius","Rotation Blast Radius When the Same Credential Is Copied Per Namespace",["secrets-management","vault","kubernetes","security"],"k8s","Every copy of a credential is one more place a rotation must reach, and the one you miss keeps working until it does not."),
 ("registry-robot-accounts","Scoped Robot Accounts Instead of a Human Credential on a CI Machine",["container-registry","secrets-management","ci-cd","security"],"k8s","A person's credential on an unattended device ties that device to an account that rotates unpredictably and grants too much."),
 ("registry-scan-wedged-state","A Wedged Scan That Blocks Every Future Scan of the Same Artifact",["container-registry","automation","security","error-handling"],"k8s","A scan stuck pending is indistinguishable from one still running, and blocks every later request for that artifact."),
 ("populated-field-is-not-evidence","A Populated Field Is Not Evidence the Thing It Names Exists",["testing","automation","api-design","error-handling"],"k8s","A non-empty summary synthesised from a failed scan passed a check meant to prove the scan succeeded."),
 ("cannot-ever-vs-currently-broken","Separating 'Cannot Ever' From 'Currently Broken' in Sweep Automation",["automation","testing","error-handling","observability"],"k8s","Treating two causes of one error code the same let a real regression pass as expected behaviour for months."),
 ("netbird-zero-trust-groups","Group-Based Default-Deny Instead of Hand-Maintained Peer Lists",["networking","netbird","zero-trust","identity"],"iac","Nobody can read a per-peer VPN config and answer who is allowed to reach the database tier."),
 ("wireguard-routing-asymmetry","WireGuard Route Selection: Per-Host /32 Versus Subnet Routes With NAT",["wireguard","networking","linux","vpn"],"iac","A subnet route to peers that are themselves mesh members breaks the return path, silently and asymmetrically."),
 ("dns-wildcard-empty-non-terminal","DNS Wildcards and Empty Non-Terminals: The Answer That Is Not an Error",["dns","tls","acme","testing"],"iac","A leftover challenge record makes its parent an empty non-terminal, and a wildcard is forbidden from answering for it."),
 ("acme-challenge-pruning","Pruning Stale ACME Challenge Records Without Killing an Issuance in Flight",["dns","acme","automation","testing"],"iac","Deleting a leftover record restores a name, but deleting one still in use costs an issuance a retry."),
 ("keycloak-realm-as-code","Realm-as-Code: Reconciling Clients and Roles But Never Users or Signing Keys",["keycloak","identity","gitops","security"],"iac","Managing a realm by hand causes drift, but naive config-as-code deletes live accounts or rotates the keys backing every token."),
 ("oidc-client-shapes","OIDC Client Shapes: A Token-Validating API Is Not a Login Client",["oidc","keycloak","security","api-design"],"iac","Treating every client the same conflates 'users log in here' with 'this validates a token', and the wrong shape accepts unintended flows."),
 ("keycloak-azp-enforcement","Enforcing Client Identity Alongside Realm Roles",["keycloak","oidc","security","fastapi"],"taberna","A realm role is not enough: a user signed into an untrusted client still carries it, so role-only checks admit the wrong caller."),
 ("forward-auth-legacy-services","Forward Auth: Putting Real Authentication in Front of Software That Has None",["keycloak","reverse-proxy","security","authentication"],"iac","The services you most want protected are often the ones that cannot speak modern auth at all."),
 ("proxy-auth-headers-trust","Trusting Proxy Auth Headers Without Implementing Authorization Twice",["authentication","reverse-proxy","security","api-design"],"k8s","A service behind an authenticating proxy needs to know who is calling without duplicating the proxy's job and disagreeing with it."),
 ("entity-graph-model","An Entity Graph as the Data Model for a Heterogeneous Estate",["observability","data-modeling","go","architecture"],"k8s","Hosts, volumes, links and alerts have different shapes but need one model with containment, edges and a uniform health rollup."),
 ("atomic-graph-swap","Merging Concurrent Polls Without Ever Serving a Half-Built Graph",["go","concurrency","observability","reliability"],"k8s","A console polling several sources must never show a state where some landed and others did not."),
 ("graceful-source-degradation","Degrading Gracefully When One Data Source Fails",["observability","reliability","go","resilience"],"k8s","A naive merge lets one upstream's outage blank entities it does not even own, turning a partial failure into a total one."),
 ("health-rules-engine","A Rules File for Health Evaluation, Validated at Load Time",["observability","rules-engine","go","health-checks"],"k8s","Hardcoding a threshold per metric does not scale, and a typo in a rules file must fail at startup rather than silently at runtime."),
 ("worst-status-rollup","Rolling Worst Status Up a Containment Tree Without Per-Type Logic",["observability","health-checks","algorithms","data-modeling"],"k8s","A parent should reflect the worst problem beneath it without hand-written rollup code for every entity type."),
 ("textfile-collector-probes","Host Probes via the Textfile Collector Pattern",["prometheus","metrics","observability","systemd"],"k8s","Custom host measurements need to reach the pipeline without writing and running a full exporter daemon."),
 ("path-mtu-binary-search","Finding Path MTU with a Binary Search Over ICMP Probes",["networking","mtu","algorithms","observability"],"k8s","Testing packet sizes linearly wastes probes; the search space has an obvious logarithmic structure."),
 ("http-service-discovery","Prometheus HTTP Service Discovery Backed by a Live Inventory",["prometheus","service-discovery","observability","monitoring"],"k8s","A hand-maintained target list drifts the moment infrastructure changes, leaving new hosts silently unmonitored."),
 ("blackbox-published-names","Probing the Names You Publish, Not the Services You Run",["monitoring","dns","observability","blackbox-monitoring"],"k8s","A service can report itself perfectly healthy while being unreachable, because everything it measures is inside the boundary that broke."),
 ("read-only-query-proxy","A Bounded Read-Only Query Proxy That Is Not a Reverse Proxy",["observability","api-security","prometheus","proxy"],"k8s","Exposing a metrics backend to a browser lets a client issue unbounded queries against internal infrastructure."),
 ("dead-mans-switch","A Dead-Man's Switch for the Monitoring System Itself",["alerting","observability","reliability","monitoring"],"iac","A monitoring pipeline cannot alert on its own total outage through the path that is down."),
 ("alertmanager-mqtt-retained","Turning Alertmanager Webhooks Into Retained MQTT State",["alerting","mqtt","observability","webhooks"],"k8s","Downstream systems want to know what is firing right now, including systems that connect after an alert started."),
 ("drift-detection-declared-observed","Detecting Drift Between a Declared Inventory and an Observed Mesh",["configuration-management","drift-detection","observability","networking"],"k8s","An inventory declares intent and the live controller knows reality, and nothing compares the two automatically."),
 ("never-reported-vs-zero","Distinguishing 'Never Reported' From 'Reported Zero' in a Dashboard",["frontend","observability","ux","data-visualization"],"k8s","Collapsing a missing sample and a genuine zero into the same digit hides exactly the problem worth seeing."),
 ("compound-graph-layout","Why Force-Directed Layouts Diverge on Compound Graphs",["frontend","graph-visualization","algorithms"],"k8s","Moving a compound parent translates its whole subtree, and those translations compound across runs."),
 ("relayout-decision","Deciding When to Re-Layout a Live-Updating Graph",["frontend","graph-visualization","ux","testing"],"k8s","Re-running layout on every refresh throws away the user's pan and zoom; never re-running hides real structural change."),
 ("canvas-pixel-testing","Testing That a Canvas Diagram Actually Painted Pixels",["testing","frontend","playwright","canvas"],"k8s","A DOM-only assertion passes while the canvas renders in an empty viewport, which is exactly what a layout bug produces."),
 ("percent-encoding-composite-ids","Percent-Encoding Composite Identifiers That Contain Separators",["rest-api","web-development","testing","api-design"],"k8s","An ID built from two fields can contain a slash, and path normalisation silently corrupts exactly those lookups."),
 ("mcp-tools-over-same-handlers","Exposing an Internal API as MCP Tools Without a Second Implementation",["mcp","api-design","go","llm"],"k8s","An agent-facing surface built separately from the human-facing one drifts apart the moment either is changed."),
 ("issue-to-pr-daemon","A Deterministic Daemon That Turns a Labelled Issue Into a Pull Request",["agents","github-automation","python","ci-cd"],"atlas","Letting a model decide what to commit and push is hard to make auditable without a strict split of side effects."),
 ("label-as-contract","The Label as Contract: Consent and Priority as the Whole Queue",["agents","github-automation","workflow-design","security"],"atlas","An autonomous worker needs an explicit, auditable opt-in per unit of work, or it acts on anything that looks prioritised."),
 ("testing-an-agent-harness","Testing an Agent Harness Without Ever Calling the Model",["testing","agents","python","dry-run"],"atlas","The daemon deciding what an agent may do is ordinary deterministic code, and testing it by running the model is slow and irreproducible."),
 ("evidence-over-assertion","Verifying an Agent's Work Against Reality, Not Its Own Report",["agents","verification","testing","git"],"atlas","A run can exit cleanly, claim success, and have changed nothing at all."),
 ("agent-permissions-inversion","From a Large Allowlist to Three Denials: Permissions for a Coding Agent",["agents","security","permissions","sandboxing"],"atlas","A deny-by-default command list becomes a treadmill, because the set a competent worker needs is not enumerable in advance."),
 ("prompt-injection-untrusted-issues","Treating Issue Bodies as Untrusted Input",["security","agents","llm","prompt-injection"],"atlas","An agent reading issues is reading text that contributors control, which is an injection surface if treated as instructions."),
 ("stuck-agent-filesystem-watch","Detecting a Stuck Agent by Watching the Filesystem, Not the Log",["agents","process-supervision","python","reliability"],"atlas","Pattern-matching log output kills runs that are actively producing work, because tools log the same line for different actions."),
 ("salvage-partial-agent-work","Salvaging a Stopped Run as a Draft Pull Request",["agents","git","github-automation","reliability"],"atlas","Discarding a killed run's uncommitted work means the next attempt starts from zero, which gets it killed again."),
 ("merge-rate-kill-switch","A Kill Switch Based on Merge Rate",["agents","automation","observability","reliability"],"atlas","An automation loop producing pull requests nobody merges will keep burning compute on work being silently rejected."),
 ("dependency-aware-queueing","Encoding 'Do Not Start This Yet' for a Chain of Dependent Issues",["agents","github-automation","workflow-design","task-scheduling"],"atlas","A worker taking the highest-priority issue every cycle will start several steps of a migration at once against a stale base."),
 ("github-api-search-scoped-discovery","Polling the GitHub API Without Tripping the Rate Limit",["github-api","rate-limiting","python","automation"],"atlas","Listing issues repository by repository scales calls with repository count, not with the work actually queued."),
 ("data-locality-by-default","One Local Endpoint for Every Agent Session",["self-hosting","privacy","llm","developer-tools"],"atlas","An IDE assistant, a terminal agent and a daemon each send code somewhere unless one thing enforces a single point of egress."),
 ("speculative-decoding","Speculative Decoding: Doubling Single-Stream Generation Throughput",["llm","gpu","performance","benchmarking"],"atlas","A single-stream local server is generation-bound, and ordinary batching does not help when only one request runs at a time."),
 ("model-swap-config-pitfall","One Unpinned Config Key That Doubles Your Model-Swap Cost",["llm","configuration","gpu","debugging"],"atlas","Agent tooling using a separate small model for background tasks turns every background call into a full reload on one GPU."),
 ("compile-time-api-documentation","Enforcing API Documentation at Compile Time in C++",["cpp","openapi","api-documentation","testing"],"horus","OpenAPI specs for hand-registered endpoints rot because nothing forces an update when an endpoint changes."),
 ("offline-swagger-ui","Serving API Documentation on a Network With No Internet Access",["openapi","docker","offline","api-documentation"],"horus","Interactive docs normally load assets from a CDN, which breaks entirely on a firewalled industrial network."),
 ("remerging-microservices","Merging Seven REST Services Into One Binary Without a Rewrite",["architecture","microservices","cpp","docker"],"horus","A fleet of small services shares an operational cost that a full rewrite would risk paying twice."),
 ("microservice-decomposition-heuristic","When Microservice Decomposition Is the Wrong Default",["architecture","microservices","docker"],"horus","Independent versioning and deployment have a real cost even when the combined logic fits one process."),
 ("angular-route-registry","One Registry for Routes, Navigation and Role Requirements",["angular","authorization","routing","testing"],"ygg","When paths, labels and role checks live in three places, a route ends up reachable without a permission check."),
 ("jemalloc-page-size","Debugging a jemalloc Page-Size Incompatibility on Newer ARM Hardware",["arm64","embedded","debugging","linux"],"horus","A newer board defaulting to a 16KB kernel page size crashes a distro-packaged service that worked unmodified on the last revision."),
 ("meta-repo-submodules","A Meta-Repository of Submodules for a Reproducible Device Build",["git","build-systems","embedded","meta-repo"],"horus","A build depending on a dozen independently versioned repos needs one checkout pinning an exact commit of each."),
 ("stacklight-commander","Driving Physical Indicator Hardware From Aggregated Health Keys",["hardware","monitoring","redis","python"],"horus","A dozen services each integrating with the alarm hardware means a dozen slightly different integrations."),
 ("inotify-config-loader","An inotify Config Loader That Turns Files Into Live Keys",["redis","configuration","python","linux"],"horus","Services reading live config need on-disk changes to take effect without a restart or a polling loop."),
 ("openapi-docs-contract-test","Keeping Documentation Honest with an OpenAPI Snapshot Diff",["documentation","testing","openapi","ci-cd"],"taberna","Hand-written docs and a live API drift apart silently, sometimes describing tables that were never built."),
 ("documentation-link-tests","Link-Index and Anchor Tests for Documentation That Rots Quietly",["documentation","testing","ci-cd","python"],"iac","A page nobody links is undiscoverable, and a link to a heading breaks the moment that heading is reworded."),
 ("testing-real-expressions","Testing Infrastructure Code by Executing Its Real Expressions",["testing","ansible","ci-cd","infrastructure-as-code"],"iac","A test that paraphrases what an expression should do drifts from the expression, and both keep passing."),
 ("privacy-first-analytics","Analytics With No Column Capable of Identifying Anyone",["privacy","analytics","data-modeling","fastapi"],"taberna","A consent banner can cost most of your sessions, corrupting the funnel it was meant to measure."),
 ("optional-opentelemetry","Wiring OpenTelemetry So a Collector Outage Is Not Your Outage",["opentelemetry","observability","python","reliability"],"taberna","An integration that raises when the collector is unreachable turns misconfigured observability into a real incident."),
 ("otel-gauges-on-a-worker","Why Business Metric Gauges Belong on a Worker, Not an SDK Callback",["opentelemetry","asyncio","observability","python"],"taberna","A gauge callback awaiting a session on an event loop it does not own registers cleanly and then reports nothing."),
 ("revisioned-time-series","A Revisioned Model for Time-Series Data That Gets Restated",["postgresql","time-series","data-modeling","python"],"taberna","Overwriting a republished value destroys the ability to know it ever changed or what was known at the time."),
 ("returns-on-immutable-orders","Modelling Returns Without Mutating the Order They Came From",["domain-modeling","postgresql","api-design"],"taberna","Bolting refund logic onto the order table blurs what was ordered with what came back."),
 ("read-only-status-endpoint","A Read-Only Status Endpoint Using Only the Standard Library",["python","observability","sqlite","daemon"],"atlas","A daemon's state is invisible from outside the process, and a web framework is disproportionate for three read-only routes."),
 ("mysql-double-fsync","A Throughput Cliff Hiding Behind Two Independent fsync Settings",["mysql","storage","performance","databases"],"k8s","Fixing one durability setting still leaves every commit synchronously flushing over a slow network device."),
 ("meta-infrastructure-overview","The Whole Estate in One Article: How Every Layer Fits Together",["infrastructure-as-code","kubernetes","ceph","observability","architecture"],"iac","Individual articles describe the parts; nothing describes how the parts became one system, or in what order they had to arrive."),
]

RENAMES = [
 ("infrastructure-as-code","Infrastructure as Code with Ansible: Making a Host Reproducible from the Repository"),
 ("kubernetes","Docker Compose or Kubernetes: What the Control Loop Actually Buys You"),
 ("ceph","Running Ceph on Commodity Hardware: Failure Domains and the Four Things That Bite"),
 ("observatory-monitoring","Alerting on Symptoms Instead of Metrics: Designing Alerts People Do Not Ignore"),
 ("netbird-vpn","Overlay Mesh Networking with NetBird: Peer Addressing and the Public DNS Fallthrough"),
 ("keycloak","Self-Hosted Identity with Keycloak: Central Offboarding and Forward Auth"),
 ("atlas-agentic-ops","Self-Hosted LLM Inference: Serving, Benchmarking and Agent Guardrails"),
 ("opentaberna","Documentation as a Repository: Publishing a Wiki.js Site from Reviewed Markdown"),
]


# Strong topics that did not fit the 100 slots. Promote any of these by swapping it for a
# scheduled one — the research behind them is identical, only the calendar is full.
BACKLOG = {
 "cpk-as-a-stream","sensor-calibration-ema","cpp-websocket-fanout","pdf-report-generation",
 "sensor-data-simulation","influxdb-attendance-events","upstream-200-for-bad-credentials",
 "design-tokens-discipline","angular-ssr-behind-proxy","power-metering-self-hosting",
 "mqtt-acls","analytics-in-sql-not-client","sql-timezone-day-bucketing",
 "adapter-interfaces-providers","pure-function-domain-logic","vcpkg-cross-compilation",
 "dual-arch-ci-pipelines",
 "registry-scan-wedged-state","populated-field-is-not-evidence","percent-encoding-composite-ids",
 "compound-graph-layout","relayout-decision","never-reported-vs-zero","cpu-feature-scheduling",
 "statefulset-immutable-resize","init-container-ordering","registry-blob-redirect",
 "mysql-double-fsync","jemalloc-page-size","meta-repo-submodules","stacklight-commander",
 "inotify-config-loader","offline-swagger-ui","compile-time-api-documentation",
 "remerging-microservices","angular-route-registry","read-only-status-endpoint",
 "returns-on-immutable-orders","revisioned-time-series","otel-gauges-on-a-worker",
 "optional-opentelemetry","privacy-first-analytics","documentation-link-tests","worst-status-rollup",
 "salvage-partial-agent-work","merge-rate-kill-switch",
 "dependency-aware-queueing","github-api-search-scoped-discovery","stuck-agent-filesystem-watch",
 "model-swap-config-pitfall","speculative-decoding","mcp-tools-over-same-handlers",
 "read-only-query-proxy","drift-detection-declared-observed","path-mtu-binary-search",
 "proxy-auth-headers-trust","secret-rotation-blast-radius","registry-robot-accounts",
 "eso-envfrom-fanout","binfmt-daemonset","cronjob-not-job-for-sweeps",
 "acme-challenge-pruning","graceful-source-degradation",
}

def slots(n):
    out, d = [], date(2025, 8, 26)
    while len(out) < n:
        if d.weekday() in (1, 4):
            out.append(d)
        d += timedelta(days=1)
    return out

for name, bucket in (("EARLY", EARLY), ("MIDDLE", MIDDLE), ("LATE", LATE)):
    for e in bucket:
        assert len(e) == 5, f"{name}: entry has {len(e)} fields, expected 5: {e[:2]}"
        assert isinstance(e[0], str) and isinstance(e[1], str), f"{name}: slug/title not str: {e[:2]}"
        assert isinstance(e[2], list), f"{name}: labels not a list on {e[0]!r}"
        assert isinstance(e[3], str) and isinstance(e[4], str), f"{name}: source/problem not str on {e[0]!r}"
seen = [e[0] for e in EARLY + MIDDLE + LATE]
assert len(seen) == len(set(seen)), f"duplicate slugs: {[s for s in seen if seen.count(s) > 1]}"

all_topics = EARLY + MIDDLE + LATE
ordered  = [e for e in all_topics if e[0] not in BACKLOG]
deferred = [e for e in all_topics if e[0] in BACKLOG]
assert len(ordered) == 100, f"expected 100 scheduled, got {len(ordered)} (backlog {len(deferred)})"

# The existing eight are folded into the timeline rather than left bunched at the end.
existing = {s: t for s, t in RENAMES}
placed = []
for i, e in enumerate(ordered):
    placed.append(e)
    # Interleave the renamed articles across the run so the blog reads as one history.
    if i in (17, 30, 44, 58, 70, 82, 92, 98):
        s = list(existing)[len([p for p in placed if p[0] in existing])]
        placed.append((s, existing[s], ["retitled"], "existing", "RETITLED — full rewrite to the four-part structure"))

dates = slots(len(placed))
plan = [{"n": i + 1, "date": dates[i].isoformat(), "slug": e[0], "title": e[1],
         "labels": e[2], "source": e[3], "problem": e[4]} for i, e in enumerate(placed)]

backlog_out = [{"slug": e[0], "title": e[1], "labels": e[2], "source": e[3], "problem": e[4]}
               for e in deferred]
out = pathlib.Path("/home/philipp/Schreibtisch/self/github-page/docs/blog-plan.json")
out.write_text(json.dumps({"scheduled": plan, "backlog": backlog_out}, indent=1))
print(f"{len(plan)} articles, {plan[0]['date']} .. {plan[-1]['date']}")
tags = sorted({t for e in placed for t in e[2]})
print(f"{len(tags)} distinct labels")
