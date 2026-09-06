# Article plan

108 articles: **100 new** plus the **8 already published**, which are rewritten and
retitled so the whole blog reads as one series rather than two.

Nothing here is written yet. Strike, reorder or retitle anything — this file is the
cheapest place to change your mind. The research behind every entry is done, so a
swap from the backlog costs nothing.

## The rules each article follows

| Rule | How it is met |
| --- | --- |
| Professional titles | Descriptive, technology named, no wordplay. The title states what the article is about, which is also what makes it findable. |
| Problem → approach → solution → conclusion | Every article carries all four sections. The problem is stated in the table below. |
| Code examples | Each article has at least one complete, runnable example with pinned versions — Dockerfiles, manifests, playbooks, scripts. |
| Reconstructable by anyone | Examples are self-contained and use no internal address, hostname, credential or topology. A reader reproduces them with public software only. |
| Indexable | Descriptive titles, per-article meta description, BlogPosting structured data, controlled labels, internal links between related articles. |
| Sensible labels | A closed 35-tag vocabulary, below. No tag is used only once. |
| Weekly cadence | Two per week, Tuesdays and Fridays, from 2025-08-26 to 2026-09-04. |

## Ordering

Articles are placed so that no article describes something that did not exist yet at
its date. Foundations first — Linux, Ansible, Docker, Python — then storage, secrets
and the C++/Redis pipeline work, then Kubernetes, the mesh, identity, the
observability platform and the agent workflows. The estate-wide overview is last,
because it links the others.

## Label vocabulary

Closed set. An article may only carry tags from it.

| Tag | Articles | Tag | Articles | Tag | Articles |
| --- | ---: | --- | ---: | --- | ---: |
| `agents` | 7 | `alerting` | 3 | `ansible` | 6 |
| `api-design` | 13 | `architecture` | 18 | `automation` | 8 |
| `ceph` | 10 | `ci-cd` | 8 | `cpp` | 2 |
| `databases` | 4 | `dns` | 6 | `docker` | 18 |
| `documentation` | 2 | `embedded` | 4 | `fastapi` | 7 |
| `frontend` | 5 | `gitops` | 7 | `go` | 6 |
| `identity` | 10 | `infrastructure-as-code` | 12 | `kubernetes` | 16 |
| `linux` | 12 | `llm` | 9 | `networking` | 9 |
| `observability` | 12 | `performance` | 15 | `python` | 16 |
| `redis` | 7 | `reliability` | 15 | `secrets-management` | 4 |
| `security` | 21 | `storage` | 10 | `testing` | 22 |
| `tls` | 4 | `vault` | 5 |  |  |

## Schedule


### August 2025

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 1 | 2025-08-26 | **Migrating the SSH Port Mid-Playbook Without Locking Ansible Out**<br>Changing sshd's port during provisioning severs the control connection mid-run unless the config check, restart, and connection update are sequenced correctly. | `ansible` `security` `linux` `infrastructure-as-code` | ai_internal_IaC |
| 2 | 2025-08-29 | **Ansible Role Idempotence: Why a changed=0 Run Is the Only Proof You Have**<br>A playbook that reports changes on every run has stopped being a drift detector, and nobody notices because it still exits zero. | `ansible` `infrastructure-as-code` `testing` | ai_internal_IaC |

### September 2025

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 3 | 2025-09-02 | **Deploying Docker Compose from Ansible Without Shelling Out**<br>Wrapping `docker compose up` in a shell task with changed_when:false can never report a real change or work in check mode. | `ansible` `docker` `infrastructure-as-code` | ai_internal_IaC |
| 4 | 2025-09-05 | **Reasserting Kernel Sysctls That Docker Silently Reverts**<br>Docker rewrites several net.ipv4 sysctls when it creates bridges, undoing boot-time hardening with no error and no log line. | `linux` `docker` `security` | ai_internal_IaC |
| 5 | 2025-09-09 | **Docker Registry Credentials for Two Local Users on One Host**<br>Docker reads the config of whoever invokes the CLI, so writing only root's credential leaves the deploy user unauthenticated and failing silently. | `docker` `ansible` `linux` | ai_internal_IaC |
| 6 | 2025-09-12 | **chmod -R and the setgid Bit: Why a Four-Digit Mode Is Not Enough**<br>A four-digit chmod does not clear setgid, so a directory keeps propagating group ownership after an apparently correct fix. | `linux` `docker` `testing` | ai-internal_k8s + observatory |
| 7 | 2025-09-16 | **A Feature-Module Layout for a FastAPI Application That Keeps Growing**<br>A single app collapses into one giant routers.py where nobody can add a feature without touching code they do not own. | `fastapi` `python` `architecture` | OpenTaberna + apps |
| 8 | 2025-09-19 | **Designing a Consistent FastAPI Response Envelope**<br>Ad-hoc return values leave every client guessing whether a payload is wrapped and every error a different shape. | `fastapi` `api-design` `python` | OpenTaberna + apps |
| 9 | 2025-09-23 | **An Exception Hierarchy and One Handler for FastAPI Error Responses**<br>Bare HTTPExceptions scattered across a codebase produce inconsistent payloads and lose the context needed to debug them. | `fastapi` `python` `api-design` | OpenTaberna + apps |
| 10 | 2025-09-26 | **Closed-Vocabulary Pydantic Validation for a Public Ingest Endpoint**<br>An endpoint anyone can POST to will accept a typo'd field and silently drop it, hiding a client bug indefinitely. | `fastapi` `api-design` | OpenTaberna + apps |
| 11 | 2025-09-30 | **Configuration Precedence: Docker Secrets, Kubernetes Secrets, Environment, .env, Default**<br>A service reading only environment variables forces every deployment target to shoehorn secrets into one mechanism, and nothing says which value won. | `python` `infrastructure-as-code` `docker` `kubernetes` | OpenTaberna + apps |

### October 2025

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 12 | 2025-10-03 | **Replacing pip, venv, flake8, black and isort with uv and Ruff**<br>requirements.txt gives no lockfile guarantee, and four separate lint tools triple the config surface for a small team. | `python` `ci-cd` `automation` | OpenTaberna + apps |
| 13 | 2025-10-07 | **A Python CLI That Scaffolds a Project Structure You Actually Want**<br>Starting a new service means recreating the same layout, Dockerfile and ignore rules by hand, or copy-pasting from the last one. | `python` `architecture` `automation` | OpenTaberna + apps |
| 14 | 2025-10-10 | **Provisioning a Single-Board Computer as an Industrial Edge Node**<br>Turning a bare carrier board into a working node is a manual sequence that differs subtly between board revisions. | `linux` `embedded` `infrastructure-as-code` | horus + edge compute |
| 15 | 2025-10-14 | **Powering and Discovering Peripherals over PoE with Multicast Announcements**<br>Separate power cabling to each sensor multiplies installation cost, and every new module otherwise needs manual addressing. | `networking` `embedded` | horus + edge compute |
| 16 | 2025-10-17 | **Managing a Shared DNS Zone Through a Replace-Everything API**<br>Registrar APIs often expose only 'replace the whole zone', so naive automation deletes every record it does not manage. | `dns` `automation` `infrastructure-as-code` `api-design` | ai_internal_IaC |
| 17 | 2025-10-21 | **A Minimal Go HTTP Service with chi, Timeouts and Graceful Shutdown**<br>ListenAndServe drops in-flight requests on SIGTERM and gives you nowhere obvious to put middleware or timeouts. | `go` `reliability` | OpenTaberna + apps |
| 18 | 2025-10-24 | **Multi-Stage Docker Builds: Shipping a Runtime Without the Toolchain**<br>Building in the same image you ship means every runtime container carries a compiler it will never use. | `docker` `performance` | horus + edge compute |
| 19 | 2025-10-28 | **Infrastructure as Code with Ansible: Making a Host Reproducible from the Repository** **↺**<br>RETITLED — full rewrite to the four-part structure | `infrastructure-as-code` `ansible` `linux` `reliability` | already published |
| 20 | 2025-10-31 | **A Layered Storage Benchmark: Isolating Device, Network and Protocol Bottlenecks**<br>One throughput number hides whether the bottleneck is a slow disk, a saturated link, or protocol contention. | `ceph` `storage` `testing` `performance` | ai_internal_IaC |

### November 2025

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 21 | 2025-11-04 | **SMR Drives in a Replicated Cluster: Finding the Disk That Ruins the Pool**<br>Drive-managed SMR disks look identical in inventory and collapse under sustained random writes, dragging the whole pool down. | `ceph` `storage` `embedded` `linux` | ai_internal_IaC |
| 22 | 2025-11-07 | **CRUSH Hybrid Rules: Pinning the Read Primary to SSD Without Rebalancing**<br>Metadata-heavy reads suffer on spinning disks, but moving the whole pool to flash triggers a rebalance there is no room for. | `ceph` `storage` `performance` | ai_internal_IaC |
| 23 | 2025-11-11 | **Capacity Planning Against the Fullest OSD, Not the Average**<br>Placement is pseudo-random, so a cluster that looks 70% full can have a disk at 90% — and one full OSD stops cluster-wide writes. | `ceph` `storage` `performance` `reliability` | ai_internal_IaC |
| 24 | 2025-11-14 | **Tuning Ceph Recovery: The Trade Between Degraded Time and Client Latency**<br>Recovery I/O arrives exactly when you have less hardware than usual; too aggressive looks like an outage, too gentle risks the second failure. | `ceph` `storage` `performance` `reliability` | ai_internal_IaC |
| 25 | 2025-11-18 | **Replacing Distributed SSH Keys with a Vault Certificate Authority**<br>Distributing and rotating static public keys across a fleet does not scale and leaves no audit trail of who accessed what. | `vault` `security` `tls` | ai_internal_IaC |
| 26 | 2025-11-21 | **Vault KV-v2: Why put Silently Wipes Every Sibling Field**<br>put replaces the whole secret, so adding one field destroys the others — and there is no single-key delete. | `vault` `secrets-management` `security` | ai_internal_IaC |
| 27 | 2025-11-25 | **Runtime Secret Injection with a Vault Agent Sidecar and a Wrapped AppRole**<br>Rendering secrets into config files at deploy time leaves plaintext on disk and makes rotation a redeploy. | `vault` `secrets-management` `security` `docker` | ai_internal_IaC |
| 28 | 2025-11-28 | **Auto-Unsealing Vault Without Cloud KMS or a TPM, Using Tang and Clevis**<br>Manual unsealing does not scale once services depend on the secret store at boot, but cloud KMS means trusting a third party. | `vault` `security` `linux` | ai_internal_IaC |

### December 2025

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 29 | 2025-12-02 | **Tracking Moving Objects with Redis Sorted Sets and an Atomic Lua Tick**<br>Tracking several objects entering and leaving a line needs 'where is everything now' and 'drop what left' without a database. | `redis` `architecture` | horus + edge compute |
| 30 | 2025-12-05 | **Chaining Single-Purpose Redis Consumers into a Processing Pipeline**<br>A monolithic 'process all the data' service becomes untestable once it does detection, tracking, calibration and statistics at once. | `redis` `architecture` `python` | horus + edge compute |
| 31 | 2025-12-09 | **A Single-Writer Rule for Configuration Held in Redis**<br>When several services write the same key, a read after a write can return another writer's value and nobody owns the truth. | `redis` `infrastructure-as-code` `architecture` `reliability` | horus + edge compute |
| 32 | 2025-12-12 | **Sizing Redis maxmemory Against a Stream Trim Threshold**<br>Trimming only runs on write, so hitting the memory cap first means the stream can never shrink and every write is refused. | `redis` `performance` `databases` `reliability` | ai-internal_k8s + observatory |
| 33 | 2025-12-16 | **Docker Compose or Kubernetes: What the Control Loop Actually Buys You** **↺**<br>RETITLED — full rewrite to the four-part structure | `kubernetes` `docker` `architecture` `gitops` | already published |
| 34 | 2025-12-19 | **UUID Case Normalization as a Recurring Cross-Service Bug**<br>An uppercase UUID from one service and a lowercase one from another create two keys instead of one, silently. | `reliability` `architecture` `python` `testing` | horus + edge compute |
| 35 | 2025-12-23 | **Migrating Redis Consumers from Python to C++ on Constrained Hardware**<br>A dozen Python consumers running continuously become the CPU and memory bottleneck on an edge device. | `cpp` `python` `redis` `performance` | horus + edge compute |
| 36 | 2025-12-26 | **A Shared Multi-Stage Base Image for a C++ Service Fleet**<br>A dozen services each rebuilding the same HTTP framework and JSON library turn a one-line change into a multi-minute build. | `cpp` `docker` `performance` | horus + edge compute |
| 37 | 2025-12-30 | **MQTT Persistent Sessions and Retained Messages for Offline Subscribers**<br>A message published while a subscriber is offline is lost under clean-session defaults, and late subscribers see no current state. | `redis` `reliability` `embedded` | ai-internal_k8s + observatory |

### January 2026

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 38 | 2026-01-02 | **Modelling an Order Lifecycle as an Explicit State Machine**<br>Letting any code path assign order.status means invalid transitions are caught only by whoever remembers to check. | `python` `architecture` `api-design` | OpenTaberna + apps |
| 39 | 2026-01-06 | **Idempotent Payment Webhooks with a Deduplication Table**<br>Providers deliver at-least-once and retry on any non-2xx, so a naive handler double-processes payments on every redelivery. | `python` `reliability` `api-design` | OpenTaberna + apps |
| 40 | 2026-01-09 | **Preventing Overselling with a Database CHECK Constraint**<br>Two customers buying the last unit at the same moment is a race that check-then-decrement code cannot reliably win. | `databases` `performance` | OpenTaberna + apps |
| 41 | 2026-01-13 | **One Dashboard on Five Unrelated Backends: Normalising at the Boundary**<br>A portal surfacing tickets, time tracking and CI status becomes an unmaintainable ad-hoc proxy without a common shape. | `fastapi` `architecture` `api-design` `frontend` | yggdrasil + nf1 website |
| 42 | 2026-01-16 | **Sizing One GPU for Local LLM Inference: VRAM, Quantisation and KV Cache**<br>Weights plus a long context and KV cache must fit fixed VRAM, and the wrong cache format silently triples latency. | `llm` `performance` | atlas |
| 43 | 2026-01-20 | **Serving Several Local Models on One GPU with On-Demand Loading**<br>One GPU holds one large model, but different tasks want different models and manual restarts are impractical. | `llm` `performance` `architecture` | atlas |
| 44 | 2026-01-23 | **An OpenAI-Compatible Proxy in Front of a Local Model Server**<br>Every tool expects the OpenAI shape, but a local server needs auth and a health check that never triggers a model load. | `llm` `fastapi` `api-design` `identity` | atlas |
| 45 | 2026-01-27 | **Making a Benchmark Deterministic Against a Server Tuned for Interactive Use**<br>Every run scores differently, so a real regression cannot be told apart from sampling noise. | `testing` `llm` | atlas |
| 46 | 2026-01-30 | **Why a Leaderboard Score Does Not Predict Your Workload**<br>A public ranking says nothing about whether a model calls your tools correctly or survives your context lengths. | `testing` `llm` | atlas |

### February 2026

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 47 | 2026-02-03 | **Scoring Tool Calls by Parsed Structure Instead of String Equality**<br>String comparison fails on equivalent arguments and passes on wrong calls that happen to contain the right substring. | `llm` `testing` | atlas |
| 48 | 2026-02-06 | **Running Ceph on Commodity Hardware: Failure Domains and the Four Things That Bite** **↺**<br>RETITLED — full rewrite to the four-part structure | `ceph` `storage` `reliability` `performance` | already published |
| 49 | 2026-02-10 | **Building the Dry-Run Path First, and Testing That It Sends Nothing**<br>A dry-run flag bolted on afterwards drifts out of sync with the real code path exactly when you need to trust it. | `testing` `python` `automation` `infrastructure-as-code` | atlas |
| 50 | 2026-02-13 | **A Confidential OIDC Client That Exists Only to Introspect Tokens**<br>An API validating a token minted for a public SPA client cannot use that client to call introspection, which itself needs authentication. | `identity` `security` | ai-internal_k8s + observatory |
| 51 | 2026-02-17 | **A 500 That Was a Misconfigured Client, Not an Auth Failure**<br>Assuming introspection always returns an `active` field turns a client misconfiguration into an unhandled error on every request. | `identity` `api-design` `testing` | ai-internal_k8s + observatory |
| 52 | 2026-02-20 | **Trusting an Internal and a Public Certificate Authority in One Process**<br>Configuring trust for an internal CA cuts the same process off from the public chain it needs to reach an external identity provider. | `tls` `security` | ai-internal_k8s + observatory |
| 53 | 2026-02-24 | **Running BuildKit as a Remote Builder Without a Docker Daemon**<br>A CI worker under containerd has no docker.sock, so buildx's default driver cannot start BuildKit at all. | `docker` `ci-cd` | ai-internal_k8s + observatory |
| 54 | 2026-02-27 | **Rootless BuildKit: What fuse-overlayfs Costs on a Cold Cache**<br>The standard BuildKit image needs a privileged context, which is unattractive on a cluster watching for privileged workloads. | `docker` `security` `performance` | ai-internal_k8s + observatory |

### March 2026

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 55 | 2026-03-03 | **Embedding a Built Frontend Into a Go Binary with go:embed**<br>Running a static file server and an API as two processes is more moving parts than the app needs, and they can version-skew. | `go` `frontend` `architecture` | ai-internal_k8s + observatory |
| 56 | 2026-03-06 | **Designing a Docker Image Build API: Job Model and Streamed Logs**<br>A build is not request/response, and a client needs to watch output live rather than poll for a final status. | `go` `docker` `api-design` | OpenTaberna + apps |
| 57 | 2026-03-10 | **Layering an Angular Application: core, shared, features and the Rule That Holds**<br>Without enforced layering, API calls end up inside presentational components and every screen becomes its own client. | `frontend` `architecture` | OpenTaberna + apps |
| 58 | 2026-03-13 | **Runtime Configuration for an Angular Container Without Rebuilding**<br>Baking a backend URL into the build means a different deployment target needs a full rebuild instead of a variable. | `frontend` `docker` `infrastructure-as-code` `networking` | OpenTaberna + apps |
| 59 | 2026-03-17 | **A Multi-Service docker-compose That a New Contributor Can Actually Start**<br>A backend needing an identity provider, a database, a cache and a time-series store is hard to stand up reproducibly. | `docker` `identity` `testing` | yggdrasil + nf1 website |
| 60 | 2026-03-20 | **Running Alembic Migrations Once, Before the Workers Fork**<br>Migrating from application code that starts N workers risks running the migration N times, or serving requests mid-migration. | `databases` `docker` | OpenTaberna + apps |
| 61 | 2026-03-24 | **The Transactional Outbox: Never Losing a Job You Already Committed**<br>Enqueueing after commit leaves a gap where the state change is permanent but the job never runs and nothing retries. | `python` `architecture` `reliability` `databases` | OpenTaberna + apps |
| 62 | 2026-03-27 | **Argo CD App-of-Apps: Bootstrapping a Bare Cluster from One Root Application**<br>A fresh cluster has nothing installed, and hand-applying manifests in the right order does not survive a rebuild. | `kubernetes` `gitops` `automation` | ai-internal_k8s + observatory |
| 63 | 2026-03-31 | **Alerting on Symptoms Instead of Metrics: Designing Alerts People Do Not Ignore** **↺**<br>RETITLED — full rewrite to the four-part structure | `observability` `alerting` `reliability` `testing` | already published |

### April 2026

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 64 | 2026-04-03 | **ApplicationSets: Onboarding a Workload With a Directory and a Pull Request**<br>Registering each new service with its own Application object is repetitive and easy to forget. | `kubernetes` `gitops` `automation` | ai-internal_k8s + observatory |
| 65 | 2026-04-07 | **Sync Waves and the CRD-Before-Consumer Ordering Problem**<br>Deploying a CRD and a resource using it in the same sync races, and pruning the CRD owner can delete every object built on it. | `kubernetes` `gitops` `testing` | ai-internal_k8s + observatory |
| 66 | 2026-04-10 | **Pinning Image Tags to Git SHAs Because GitOps Diffs Manifests, Not Registries**<br>With a floating tag, CI can push a new image and nothing in the manifest changes, so no rollout happens. | `gitops` `ci-cd` `docker` | ai-internal_k8s + observatory |
| 67 | 2026-04-14 | **Breaking the Circular Dependency in GitOps Secret Delivery**<br>A secrets operator deployed by the GitOps controller cannot also deliver that controller's own login secret. | `kubernetes` `gitops` `secrets-management` | ai_internal_IaC |
| 68 | 2026-04-17 | **Ceph CSI StorageClasses: RBD for Block, CephFS for Shared Volumes**<br>Databases need exclusive block devices and multi-pod workloads need shared volumes, from one storage cluster. | `kubernetes` `ceph` `storage` | ai-internal_k8s + observatory |
| 69 | 2026-04-21 | **Importing Pre-Existing Storage as a Static PersistentVolume**<br>Data that already exists outside Kubernetes must be mountable without being provisioned, and must survive a mistaken delete. | `kubernetes` `storage` `ceph` `reliability` | ai-internal_k8s + observatory |
| 70 | 2026-04-24 | **Object Storage as the Durability Boundary for a Container Registry**<br>Blobs on a cluster-managed volume are one reclaim-policy or prune mistake away from deletion. | `kubernetes` `storage` `docker` | ai-internal_k8s + observatory |
| 71 | 2026-04-28 | **fsGroup Recursive chown Hangs on Large Volumes**<br>Kubernetes chowns every file at mount time, which can hang pod startup for hours on a volume with millions of files. | `kubernetes` `storage` `ceph` `performance` | ai-internal_k8s + observatory |

### May 2026

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 72 | 2026-05-01 | **A DNS Canary CronJob as a Regression Test for Split-Horizon Resolution**<br>Pods can inherit a search domain that resolves public names to an internal blackhole, and the regression can silently reappear. | `kubernetes` `dns` `observability` `testing` | ai-internal_k8s + observatory |
| 73 | 2026-05-05 | **ndots and the Accidental Search-Domain Leak in Pod DNS**<br>A pod's search list can append an internal domain to every external lookup, resolving it to nothing useful or worse. | `kubernetes` `dns` `networking` | ai-internal_k8s + observatory |
| 74 | 2026-05-08 | **How a VPN Client Silently Shrinks Your Pod Network MTU**<br>A CNI auto-detecting MTU from the lowest interface drops the pod network to fragmented packets with no error, just worse throughput. | `kubernetes` `networking` | ai_internal_IaC |
| 75 | 2026-05-12 | **cert-manager for Mesh-Only Names That No ACME Challenge Can Reach**<br>Internal services need TLS but have no public record for an HTTP-01 or DNS-01 challenge to validate against. | `kubernetes` `tls` | ai-internal_k8s + observatory |
| 76 | 2026-05-15 | **Overlay Mesh Networking with NetBird: Peer Addressing and the Public DNS Fallthrough** **↺**<br>RETITLED — full rewrite to the four-part structure | `networking` `dns` `security` `linux` | already published |
| 77 | 2026-05-19 | **External Secrets with Offline JWT Validation When Vault Cannot Reach the Cluster**<br>The standard Kubernetes auth method needs a callback into the API server, which fails when the two are isolated by design. | `kubernetes` `vault` `secrets-management` `security` | ai-internal_k8s + observatory |
| 78 | 2026-05-22 | **Separating 'Cannot Ever' From 'Currently Broken' in Sweep Automation**<br>Treating two causes of one error code the same let a real regression pass as expected behaviour for months. | `automation` `testing` `api-design` `observability` | ai-internal_k8s + observatory |
| 79 | 2026-05-26 | **Group-Based Default-Deny Instead of Hand-Maintained Peer Lists**<br>Nobody can read a per-peer VPN config and answer who is allowed to reach the database tier. | `networking` `identity` | ai_internal_IaC |
| 80 | 2026-05-29 | **WireGuard Route Selection: Per-Host /32 Versus Subnet Routes With NAT**<br>A subnet route to peers that are themselves mesh members breaks the return path, silently and asymmetrically. | `networking` `linux` | ai_internal_IaC |

### June 2026

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 81 | 2026-06-02 | **DNS Wildcards and Empty Non-Terminals: The Answer That Is Not an Error**<br>A leftover challenge record makes its parent an empty non-terminal, and a wildcard is forbidden from answering for it. | `dns` `tls` `testing` | ai_internal_IaC |
| 82 | 2026-06-05 | **Realm-as-Code: Reconciling Clients and Roles But Never Users or Signing Keys**<br>Managing a realm by hand causes drift, but naive config-as-code deletes live accounts or rotates the keys backing every token. | `identity` `gitops` `security` | ai_internal_IaC |
| 83 | 2026-06-09 | **OIDC Client Shapes: A Token-Validating API Is Not a Login Client**<br>Treating every client the same conflates 'users log in here' with 'this validates a token', and the wrong shape accepts unintended flows. | `identity` `security` `api-design` | ai_internal_IaC |
| 84 | 2026-06-12 | **Enforcing Client Identity Alongside Realm Roles**<br>A realm role is not enough: a user signed into an untrusted client still carries it, so role-only checks admit the wrong caller. | `identity` `security` `fastapi` | OpenTaberna + apps |
| 85 | 2026-06-16 | **Forward Auth: Putting Real Authentication in Front of Software That Has None**<br>The services you most want protected are often the ones that cannot speak modern auth at all. | `identity` `networking` `security` | ai_internal_IaC |
| 86 | 2026-06-19 | **An Entity Graph as the Data Model for a Heterogeneous Estate**<br>Hosts, volumes, links and alerts have different shapes but need one model with containment, edges and a uniform health rollup. | `observability` `architecture` `go` | ai-internal_k8s + observatory |
| 87 | 2026-06-23 | **Merging Concurrent Polls Without Ever Serving a Half-Built Graph**<br>A console polling several sources must never show a state where some landed and others did not. | `go` `performance` `observability` `reliability` | ai-internal_k8s + observatory |
| 88 | 2026-06-26 | **A Rules File for Health Evaluation, Validated at Load Time**<br>Hardcoding a threshold per metric does not scale, and a typo in a rules file must fail at startup rather than silently at runtime. | `observability` `go` | ai-internal_k8s + observatory |
| 89 | 2026-06-30 | **Self-Hosted Identity with Keycloak: Central Offboarding and Forward Auth** **↺**<br>RETITLED — full rewrite to the four-part structure | `identity` `security` `architecture` | already published |

### July 2026

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 90 | 2026-07-03 | **Host Probes via the Textfile Collector Pattern**<br>Custom host measurements need to reach the pipeline without writing and running a full exporter daemon. | `observability` `linux` | ai-internal_k8s + observatory |
| 91 | 2026-07-07 | **Prometheus HTTP Service Discovery Backed by a Live Inventory**<br>A hand-maintained target list drifts the moment infrastructure changes, leaving new hosts silently unmonitored. | `observability` `automation` `networking` | ai-internal_k8s + observatory |
| 92 | 2026-07-10 | **Probing the Names You Publish, Not the Services You Run**<br>A service can report itself perfectly healthy while being unreachable, because everything it measures is inside the boundary that broke. | `observability` `dns` | ai-internal_k8s + observatory |
| 93 | 2026-07-14 | **A Dead-Man's Switch for the Monitoring System Itself**<br>A monitoring pipeline cannot alert on its own total outage through the path that is down. | `alerting` `observability` `reliability` | ai_internal_IaC |
| 94 | 2026-07-17 | **Turning Alertmanager Webhooks Into Retained MQTT State**<br>Downstream systems want to know what is firing right now, including systems that connect after an alert started. | `alerting` `redis` `observability` `api-design` | ai-internal_k8s + observatory |
| 95 | 2026-07-21 | **Testing That a Canvas Diagram Actually Painted Pixels**<br>A DOM-only assertion passes while the canvas renders in an empty viewport, which is exactly what a layout bug produces. | `testing` `frontend` | ai-internal_k8s + observatory |
| 96 | 2026-07-24 | **A Deterministic Daemon That Turns a Labelled Issue Into a Pull Request**<br>Letting a model decide what to commit and push is hard to make auditable without a strict split of side effects. | `agents` `python` `ci-cd` | atlas |
| 97 | 2026-07-28 | **The Label as Contract: Consent and Priority as the Whole Queue**<br>An autonomous worker needs an explicit, auditable opt-in per unit of work, or it acts on anything that looks prioritised. | `agents` `security` | atlas |
| 98 | 2026-07-31 | **Testing an Agent Harness Without Ever Calling the Model**<br>The daemon deciding what an agent may do is ordinary deterministic code, and testing it by running the model is slow and irreproducible. | `testing` `agents` `python` | atlas |

### August 2026

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 99 | 2026-08-04 | **Verifying an Agent's Work Against Reality, Not Its Own Report**<br>A run can exit cleanly, claim success, and have changed nothing at all. | `agents` `testing` `ci-cd` | atlas |
| 100 | 2026-08-07 | **Self-Hosted LLM Inference: Serving, Benchmarking and Agent Guardrails** **↺**<br>RETITLED — full rewrite to the four-part structure | `llm` `agents` `testing` `security` | already published |
| 101 | 2026-08-11 | **From a Large Allowlist to Three Denials: Permissions for a Coding Agent**<br>A deny-by-default command list becomes a treadmill, because the set a competent worker needs is not enumerable in advance. | `agents` `security` `linux` | atlas |
| 102 | 2026-08-14 | **Treating Issue Bodies as Untrusted Input**<br>An agent reading issues is reading text that contributors control, which is an injection surface if treated as instructions. | `security` `agents` `llm` | atlas |
| 103 | 2026-08-18 | **One Local Endpoint for Every Agent Session**<br>An IDE assistant, a terminal agent and a daemon each send code somewhere unless one thing enforces a single point of egress. | `llm` `security` `python` | atlas |
| 104 | 2026-08-21 | **When Microservice Decomposition Is the Wrong Default**<br>Independent versioning and deployment have a real cost even when the combined logic fits one process. | `architecture` `docker` | horus + edge compute |
| 105 | 2026-08-25 | **Keeping Documentation Honest with an OpenAPI Snapshot Diff**<br>Hand-written docs and a live API drift apart silently, sometimes describing tables that were never built. | `documentation` `testing` `ci-cd` | OpenTaberna + apps |
| 106 | 2026-08-28 | **Testing Infrastructure Code by Executing Its Real Expressions**<br>A test that paraphrases what an expression should do drifts from the expression, and both keep passing. | `testing` `ansible` `ci-cd` `infrastructure-as-code` | ai_internal_IaC |

### September 2026

| # | Date | Title | Labels | Source |
| ---: | --- | --- | --- | --- |
| 107 | 2026-09-01 | **Documentation as a Repository: Publishing a Wiki.js Site from Reviewed Markdown** **↺**<br>RETITLED — full rewrite to the four-part structure | `documentation` `testing` `ci-cd` `architecture` | already published |
| 108 | 2026-09-04 | **The Whole Estate in One Article: How Every Layer Fits Together**<br>Individual articles describe the parts; nothing describes how the parts became one system, or in what order they had to arrive. | `infrastructure-as-code` `kubernetes` `ceph` `observability` `architecture` | ai_internal_IaC |

**↺** marks one of the eight already-published articles, retitled and rewritten.

## Backlog

63 topics that did not fit the 108 slots. The research is identical;
only the calendar is full. Swap any of these for a scheduled one.

| Title | Labels | Source |
| --- | --- | --- |
| Per-Channel Polynomial Calibration and EMA Smoothing in One Pipeline Stage<br>Recalculating calibration in every consumer duplicates both the logic and the coefficients. | `embedded` `python` `redis` | horus + edge compute |
| Computing Cpk Continuously Instead of as a Batch Report<br>Process capability is traditionally an offline report, but an operator watching a live line needs a current value per zone. | `performance` `observability` `redis` `python` | horus + edge compute |
| Cross-Compiling a vcpkg C++ Project to ARM64<br>A dependency's build step tries to run a host binary that does not exist for the target, and the fix is an environment variable. | `cpp` `ci-cd` `embedded` | horus + edge compute |
| Fanning Redis Pub/Sub Out to Many WebSocket Clients in C++<br>Every browser tab holding its own Redis subscription does not scale and exposes the store to untrusted clients. | `cpp` `redis` `performance` | horus + edge compute |
| Per-Account MQTT ACLs as the Real Authorization Boundary<br>A shared broker serving many flows needs each account restricted, or one bug reads or spoofs another flow's messages. | `redis` `identity` `security` `embedded` | ai-internal_k8s + observatory |
| Timezone-Correct Day Bucketing for Revenue Reporting<br>Grouping by day on UTC timestamps quietly moves evening orders into the next day for any shop east of Greenwich. | `databases` `architecture` | OpenTaberna + apps |
| Computing Analytics in SQL Instead of From the Last Page of Results<br>Deriving revenue in the frontend from the last N orders gives numbers that stop being true once volume passes the page size. | `databases` `fastapi` | OpenTaberna + apps |
| Adapter Interfaces for Swappable Payment, Carrier and Mail Providers<br>Calling a vendor SDK from business logic means a provider swap is a rewrite of every call site, not a new class. | `python` `architecture` `testing` `api-design` | OpenTaberna + apps |
| Storing Time-Attendance Events in InfluxDB and Rolling Them Up on Read<br>Clock-in events are time-series data, and modelling them relationally makes 'hours this week' slow and verbose. | `databases` `fastapi` `frontend` | yggdrasil + nf1 website |
| When an Upstream Answers 200 for Wrong Credentials<br>Some registry APIs return 200 and an empty catalog whether you are unauthenticated or simply wrong, so integration reports 'connected, nothing here'. | `api-design` `identity` `reliability` `fastapi` | yggdrasil + nf1 website |
| Metering GPU and CPU Power to a Currency Figure<br>The case for self-hosting is unclear without a measured cost per session, and most setups have no wall-power sensor. | `observability` `llm` `python` | atlas |
| Building One Tag Natively on Two CPU Architectures<br>An amd64 runner building arm64 under emulation can be an order of magnitude slower, which matters for a fleet of edge devices. | `ci-cd` `docker` `embedded` | horus + edge compute |
| Design Tokens Over Literal Colours in Templates<br>Hex values scattered across templates make a UI drift screen by screen and 'what does this colour mean' stops having an answer. | `frontend` `architecture` `documentation` | OpenTaberna + apps |
| Server-Side Rendering Angular Behind a Reverse Proxy<br>SSR turns the frontend into a long-running process that must be reachable from the proxy, unlike a static bundle. | `frontend` `networking` `docker` | yggdrasil + nf1 website |
| Generating a Print-Ready PDF Report from JSON Without a Browser<br>Operators need a printable record with a chart and thresholds, and a headless browser is a heavy dependency for it. | `python` `fastapi` `documentation` | horus + edge compute |
| Simulating Sensor Data So the Pipeline Can Be Tested Without Hardware<br>Testing an event pipeline end to end normally needs physical hardware on a bench, which cannot run in CI. | `testing` `redis` `embedded` | horus + edge compute |
| Testing Domain Calculations as Pure Functions Without a Database<br>Business calculations written as methods on a database model need a session and fixtures to test what is really arithmetic. | `python` `testing` `architecture` | OpenTaberna + apps |
| Registry Blob Redirects That Point Somewhere the Client Cannot Reach<br>An S3-backed registry can redirect a download to an internal endpoint that external clients cannot resolve. | `docker` `storage` `networking` | ai-internal_k8s + observatory |
| An Init Container That Cannot Fix What It Runs Before<br>A repair container that runs once at pod creation cannot re-run when the main container crashloops, and can succeed vacuously. | `kubernetes` `reliability` `docker` `testing` | ai-internal_k8s + observatory |
| A DaemonSet That Registers a Kernel Feature and Then Does Nothing<br>Cross-architecture builds need binfmt handlers registered as host kernel state, which is lost on every reboot. | `kubernetes` `docker` | ai-internal_k8s + observatory |
| Resizing a Volume Behind an Immutable StatefulSet Template<br>volumeClaimTemplates storage size is immutable, so growing a PVC leaves the manifest permanently disagreeing with reality. | `kubernetes` `storage` `architecture` | ai-internal_k8s + observatory |
| Why a Sweep Belongs in a CronJob and Not a Job<br>A failed one-shot Job is terminally unhealthy and blocks reconciliation of the whole application until someone deletes it. | `kubernetes` `automation` `gitops` `reliability` | ai-internal_k8s + observatory |
| CPU Instruction Sets as a Scheduling Constraint<br>A chart's default dependency can crash with an illegal instruction on older CPUs, looking like a random crash rather than an incompatibility. | `kubernetes` `embedded` `reliability` | ai-internal_k8s + observatory |
| One ExternalSecret, Many Environment Variables<br>Wiring a dozen values through repeated secretKeyRef entries is verbose and drifts between Deployments. | `kubernetes` `secrets-management` `vault` | ai-internal_k8s + observatory |
| Rotation Blast Radius When the Same Credential Is Copied Per Namespace<br>Every copy of a credential is one more place a rotation must reach, and the one you miss keeps working until it does not. | `secrets-management` `vault` `kubernetes` `security` | ai-internal_k8s + observatory |
| Scoped Robot Accounts Instead of a Human Credential on a CI Machine<br>A person's credential on an unattended device ties that device to an account that rotates unpredictably and grants too much. | `docker` `secrets-management` `ci-cd` `security` | ai-internal_k8s + observatory |
| A Wedged Scan That Blocks Every Future Scan of the Same Artifact<br>A scan stuck pending is indistinguishable from one still running, and blocks every later request for that artifact. | `docker` `automation` `security` `api-design` | ai-internal_k8s + observatory |
| A Populated Field Is Not Evidence the Thing It Names Exists<br>A non-empty summary synthesised from a failed scan passed a check meant to prove the scan succeeded. | `testing` `automation` `api-design` | ai-internal_k8s + observatory |
| Pruning Stale ACME Challenge Records Without Killing an Issuance in Flight<br>Deleting a leftover record restores a name, but deleting one still in use costs an issuance a retry. | `dns` `tls` `automation` `testing` | ai_internal_IaC |
| Trusting Proxy Auth Headers Without Implementing Authorization Twice<br>A service behind an authenticating proxy needs to know who is calling without duplicating the proxy's job and disagreeing with it. | `identity` `networking` `security` `api-design` | ai-internal_k8s + observatory |
| Degrading Gracefully When One Data Source Fails<br>A naive merge lets one upstream's outage blank entities it does not even own, turning a partial failure into a total one. | `observability` `reliability` `go` | ai-internal_k8s + observatory |
| Rolling Worst Status Up a Containment Tree Without Per-Type Logic<br>A parent should reflect the worst problem beneath it without hand-written rollup code for every entity type. | `observability` `performance` `architecture` | ai-internal_k8s + observatory |
| Finding Path MTU with a Binary Search Over ICMP Probes<br>Testing packet sizes linearly wastes probes; the search space has an obvious logarithmic structure. | `networking` `performance` `observability` | ai-internal_k8s + observatory |
| A Bounded Read-Only Query Proxy That Is Not a Reverse Proxy<br>Exposing a metrics backend to a browser lets a client issue unbounded queries against internal infrastructure. | `observability` `security` `api-design` | ai-internal_k8s + observatory |
| Detecting Drift Between a Declared Inventory and an Observed Mesh<br>An inventory declares intent and the live controller knows reality, and nothing compares the two automatically. | `infrastructure-as-code` `observability` `networking` | ai-internal_k8s + observatory |
| Distinguishing 'Never Reported' From 'Reported Zero' in a Dashboard<br>Collapsing a missing sample and a genuine zero into the same digit hides exactly the problem worth seeing. | `frontend` `observability` | ai-internal_k8s + observatory |
| Why Force-Directed Layouts Diverge on Compound Graphs<br>Moving a compound parent translates its whole subtree, and those translations compound across runs. | `frontend` `performance` | ai-internal_k8s + observatory |
| Deciding When to Re-Layout a Live-Updating Graph<br>Re-running layout on every refresh throws away the user's pan and zoom; never re-running hides real structural change. | `frontend` `testing` | ai-internal_k8s + observatory |
| Percent-Encoding Composite Identifiers That Contain Separators<br>An ID built from two fields can contain a slash, and path normalisation silently corrupts exactly those lookups. | `api-design` `frontend` `testing` | ai-internal_k8s + observatory |
| Exposing an Internal API as MCP Tools Without a Second Implementation<br>An agent-facing surface built separately from the human-facing one drifts apart the moment either is changed. | `llm` `api-design` `go` | ai-internal_k8s + observatory |
| Detecting a Stuck Agent by Watching the Filesystem, Not the Log<br>Pattern-matching log output kills runs that are actively producing work, because tools log the same line for different actions. | `agents` `reliability` `python` | atlas |
| Salvaging a Stopped Run as a Draft Pull Request<br>Discarding a killed run's uncommitted work means the next attempt starts from zero, which gets it killed again. | `agents` `ci-cd` `reliability` | atlas |
| A Kill Switch Based on Merge Rate<br>An automation loop producing pull requests nobody merges will keep burning compute on work being silently rejected. | `agents` `automation` `observability` `reliability` | atlas |
| Encoding 'Do Not Start This Yet' for a Chain of Dependent Issues<br>A worker taking the highest-priority issue every cycle will start several steps of a migration at once against a stale base. | `agents` `automation` `architecture` | atlas |
| Polling the GitHub API Without Tripping the Rate Limit<br>Listing issues repository by repository scales calls with repository count, not with the work actually queued. | `agents` `api-design` `python` `automation` | atlas |
| Speculative Decoding: Doubling Single-Stream Generation Throughput<br>A single-stream local server is generation-bound, and ordinary batching does not help when only one request runs at a time. | `llm` `performance` `testing` | atlas |
| One Unpinned Config Key That Doubles Your Model-Swap Cost<br>Agent tooling using a separate small model for background tasks turns every background call into a full reload on one GPU. | `llm` `infrastructure-as-code` `reliability` | atlas |
| Enforcing API Documentation at Compile Time in C++<br>OpenAPI specs for hand-registered endpoints rot because nothing forces an update when an endpoint changes. | `cpp` `documentation` `testing` | horus + edge compute |
| Serving API Documentation on a Network With No Internet Access<br>Interactive docs normally load assets from a CDN, which breaks entirely on a firewalled industrial network. | `documentation` `docker` | horus + edge compute |
| Merging Seven REST Services Into One Binary Without a Rewrite<br>A fleet of small services shares an operational cost that a full rewrite would risk paying twice. | `architecture` `cpp` `docker` | horus + edge compute |
| One Registry for Routes, Navigation and Role Requirements<br>When paths, labels and role checks live in three places, a route ends up reachable without a permission check. | `frontend` `identity` `testing` | yggdrasil + nf1 website |
| Debugging a jemalloc Page-Size Incompatibility on Newer ARM Hardware<br>A newer board defaulting to a 16KB kernel page size crashes a distro-packaged service that worked unmodified on the last revision. | `embedded` `reliability` `linux` | horus + edge compute |
| A Meta-Repository of Submodules for a Reproducible Device Build<br>A build depending on a dozen independently versioned repos needs one checkout pinning an exact commit of each. | `ci-cd` `embedded` | horus + edge compute |
| Driving Physical Indicator Hardware From Aggregated Health Keys<br>A dozen services each integrating with the alarm hardware means a dozen slightly different integrations. | `embedded` `observability` `redis` `python` | horus + edge compute |
| An inotify Config Loader That Turns Files Into Live Keys<br>Services reading live config need on-disk changes to take effect without a restart or a polling loop. | `redis` `infrastructure-as-code` `python` `linux` | horus + edge compute |
| Link-Index and Anchor Tests for Documentation That Rots Quietly<br>A page nobody links is undiscoverable, and a link to a heading breaks the moment that heading is reworded. | `documentation` `testing` `ci-cd` `python` | ai_internal_IaC |
| Analytics With No Column Capable of Identifying Anyone<br>A consent banner can cost most of your sessions, corrupting the funnel it was meant to measure. | `security` `databases` `architecture` `fastapi` | OpenTaberna + apps |
| Wiring OpenTelemetry So a Collector Outage Is Not Your Outage<br>An integration that raises when the collector is unreachable turns misconfigured observability into a real incident. | `observability` `python` `reliability` | OpenTaberna + apps |
| Why Business Metric Gauges Belong on a Worker, Not an SDK Callback<br>A gauge callback awaiting a session on an event loop it does not own registers cleanly and then reports nothing. | `observability` `python` | OpenTaberna + apps |
| A Revisioned Model for Time-Series Data That Gets Restated<br>Overwriting a republished value destroys the ability to know it ever changed or what was known at the time. | `databases` `architecture` `python` | OpenTaberna + apps |
| Modelling Returns Without Mutating the Order They Came From<br>Bolting refund logic onto the order table blurs what was ordered with what came back. | `architecture` `databases` `api-design` | OpenTaberna + apps |
| A Read-Only Status Endpoint Using Only the Standard Library<br>A daemon's state is invisible from outside the process, and a web framework is disproportionate for three read-only routes. | `python` `observability` `databases` | atlas |
| A Throughput Cliff Hiding Behind Two Independent fsync Settings<br>Fixing one durability setting still leaves every commit synchronously flushing over a slow network device. | `databases` `storage` `performance` | ai-internal_k8s + observatory |

## Sources

| Repository | Articles | Visibility |
| --- | ---: | --- |
| ai-internal_k8s + observatory | 30 | private |
| ai_internal_IaC | 26 | private |
| OpenTaberna + apps | 18 | OpenTaberna public, others mixed |
| atlas | 14 | private |
| horus + edge compute | 10 | private |
| already published | 8 | — |
| yggdrasil + nf1 website | 2 | private |

Four of the source repositories are private company repositories. **No address,
hostname, credential, port, topology detail or customer name from any of them appears
in any article.** The articles carry the engineering and the reasoning; the code
examples are written from scratch to be reproducible by a stranger, which is a
stricter requirement than anonymisation and happens to satisfy both.
