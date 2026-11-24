const { H1, H2, H3, P, UL, OL, CODE, EQ, FIG, TAB } = require("./lib");

module.exports = {
  labels: { lang: "en-US", abstract: "Abstract", keywords: "Index Terms", references: "References",
            figIn: "Fig.", figCap: "Fig.", tabIn: "Table", tabCap: "TABLE" },
  meta: {
    title: "Distributed Face Recognition for a Multi-Branch Coffeehouse Chain over a Message Broker",
    authors: [
      { name: "Le Chi Tam", id: "2302220" },
      { name: "Trieu Minh Thuan", id: "2302059" },
      { name: "Nguyen Hao Khang", id: "2302319" },
    ],
    idLabel: "Student ID",
    courseLine: "CS401V – Distributed Systems, TTU",
    note: "Course report for Lab Assignment 2, Fall 2026",
  },
  abstract:
    "This paper presents a face-recognition system for a coffeehouse chain with several branches, built as a distributed " +
    "system. When a customer arrives, the branch takes a photo and sends it to a central server through the RabbitMQ " +
    "message broker. Several identical workers share the incoming requests. A worker finds the face with InsightFace, " +
    "turns it into a 512-dimensional vector and looks for the closest stored vector in PostgreSQL with pgvector. A known " +
    "customer gets their latest order back from MySQL; an unknown person is registered as a new customer. The project " +
    "concentrates on distributed-systems problems rather than recognition accuracy: adding workers to raise capacity " +
    "(competing consumers), holding bursts of requests in a queue, handing the request of a crashed worker to another " +
    "worker (manual acknowledgements), processing a repeated request only once (idempotency), keeping two databases " +
    "consistent without a shared transaction (a saga), stopping two workers from creating the same customer (an advisory " +
    "lock), and giving every branch its own account with minimal rights. On one 6-core machine, the default threading of " +
    "ONNX Runtime prevented scaling: throughput fell from 6.72 to 5.49 requests per second when going from one to three " +
    "workers. With each worker limited to two threads, 2, 4 and 6 workers reached 1.77, 2.40 and 2.60 times the " +
    "throughput of one worker. Killing a worker in the middle of a request lost no request and created no duplicate record.",
  keywords:
    "distributed systems, message queue, RabbitMQ, face recognition, scalability, fault tolerance, idempotency, saga, pgvector",

  blocks: [
    // ============================================================ I
    H1("Introduction"),
    P("A coffeehouse chain with several branches wants to recognize returning customers as they walk in and to recall " +
      "their most recent order. The assignment asks for a distributed system in which (i) a server performs face " +
      "recognition and, for a recognized customer, returns the latest order to the branch for confirmation, or, for an " +
      "unknown face, reports this and stores the person as a new customer; (ii) every branch runs a client application " +
      "that captures a photo and sends a recognition request; and (iii) an open-source recognition model, an open-source " +
      "socket or message-broker layer and a self-designed database are used."),
    P("Beyond a working recognizer, our goal is to exhibit the defining properties of a distributed system: concurrent " +
      "requests from several branches, scaling out by adding workers, tolerating worker crashes, and keeping data " +
      "consistent across two databases that do not share a transaction. The contributions are:"),
    UL("a broker-based architecture in which stateless workers consume requests as competing consumers and clients get " +
       "RPC semantics through `correlation_id` and `reply_to` (Sections III and IV);",
       "a set of mechanisms for the distributed issues above: manual acknowledgement after reply, quorum queues with a " +
       "dead-letter queue, idempotency by request ID, a saga across MySQL and pgvector, and advisory-lock mutual " +
       "exclusion (Section V);",
       "an experimental study on the real stack, including a thread-oversubscription pitfall that defeats scale-out on a " +
       "single machine, and fault-injection results (Section VII)."),
    P("Section II states the requirements, Sections III–VI describe the architecture, design, mechanisms and " +
      "implementation, Section VII reports the experiments, and Sections VIII–IX discuss limitations and conclude."),

    // ============================================================ II
    H1("Requirements and Design Goals"),
    P("{tab:req} lists the functional (F) and non-functional (N) requirements and how each is verified. The " +
      "non-functional requirements are the focus of the project."),
    TAB("req", "Requirements and verification", [0.09, 0.57, 0.34], ["l", "l", "l"],
      ["ID", "Requirement", "Verified by"], [
        ["F1", "Branch client captures a photo and sends a request", "GUI / CLI"],
        ["F2", "Recognize a returning customer, return last order", "Flow A"],
        ["F3", "New customer: report it and enroll for later", "Flow B"],
        ["F4", "Store a customer’s order", "Flow C"],
        ["F5", "Handle no-face, blurry, multi-face images", "`NO_FACE`"],
        ["F6", "Update customer name and phone", "`customer.update`"],
        ["N1", "**Scalability:** more workers, more throughput", "req/s, 1–6 workers"],
        ["N2", "**Absorb bursts** without errors", "queue depth"],
        ["N3", "**Fault tolerance:** no request lost on a crash", "lost requests = 0"],
        ["N4", "**Safe retries:** no duplicate customer or order", "duplicates = 0"],
        ["N5", "**Consistency** of MySQL and pgvector", "orphan records"],
        ["N6", "**Security:** authN/authZ, encryption", "Branch accounts and login; TLS untested"],
        ["N7", "**Heterogeneity:** language-independent clients", "AMQP + JSON"],
      ]),
    P("Constraints: an open-source model, an open-source broker and a self-designed schema; a live demonstration on " +
      "several physical machines. A face is sensitive biometric data, so photos are collected only with the subject’s " +
      "consent and the photo directory has restricted access."),

    // ============================================================ III
    H1("System Architecture"),
    H2("Overview"),
    P("The system follows a multiple-client/multiple-server model built on message-oriented middleware and can be " +
      "viewed as a multitier chain: branch clients → broker → workers → two databases ({fig:arch}). A worker is a server " +
      "for the branches and a client of the databases {cite:coulouris,tanenbaum}. In terms of the platform / middleware / " +
      "application layering {cite:coulouris}, we take the platform to be the operating system, TCP/IP and Docker; the " +
      "middleware is RabbitMQ {cite:rabbitmq}, which hides network details and provides asynchronous RPC through " +
      "`correlation_id` and `reply_to`; the application is the branch client and the recognition worker."),
    FIG("arch", "architecture.png", "System architecture. Four branch clients talk only to the broker. Identical, stateless workers share the requests, and every worker reads and writes the same two databases and photo store."),
    H2("Why a Message Broker Instead of Raw Sockets"),
    P("With raw sockets we would have to implement burst-absorbing queues, acknowledgements with redelivery, load " +
      "balancing across workers, dead-lettering and monitoring ourselves, and most of the project time would go to " +
      "infrastructure rather than distributed-systems problems. RabbitMQ provides durable queues (bursts), manual " +
      "acknowledgements (redelivery on failure), competing consumers with `prefetch` (balancing), dead-letter " +
      "exchanges (poison messages), a management UI and AMQP clients in many languages. The broker also decouples " +
      "clients and workers in space and time: a branch need not know how many workers exist, or whether one is running " +
      "at that moment."),
    H2("Technology Choices"),
    P("The broker is RabbitMQ 3.13 with quorum queues. Recognition uses InsightFace `buffalo_l` {cite:insightface}: " +
      "the SCRFD detector {cite:scrfd} and ArcFace 512-d embeddings {cite:arcface}, executed by ONNX Runtime on CPU " +
      "{cite:onnx}. Face vectors live in PostgreSQL 16 with pgvector and an HNSW cosine index {cite:pgvector,hnsw}; " +
      "customers, photos, orders and recognition logs live in MySQL 8. Worker and client are written in Python and " +
      "deployed with Docker Compose (`--scale worker=N`). The pretrained `buffalo_l` models are licensed for non-commercial " +
      "research only; this project is for education, and a commercial deployment would need another model or licence."),
    P("*Why two databases?* pgvector specializes in similarity search; MySQL is strong at relational data and " +
      "transactions. The vector store returns only a `customer_id`; everything else is in MySQL. The price is the loss of " +
      "a shared transaction, handled in Section V-E."),

    // ============================================================ IV
    H1("Detailed Design"),
    H2("Messaging Topology"),
    P("Clients may publish only to the direct exchange `coffee.requests`, which routes to two durable quorum queues, " +
      "`face.requests` (recognition) and `orders` (writes: create order, update customer). Both queues dead-letter to " +
      "`coffee.dlx`, which feeds `face.requests.dlq` and `orders.dlq` for rejected or over-delivered messages. Each " +
      "client owns an exclusive, auto-delete reply queue `reply.<branch>.<uuid>` on which the result returns, matched " +
      "by `correlation_id`. {fig:mq} shows this topology together with what a branch account is allowed to do."),
    FIG("mq", "rabbitmq_topology.png", "RabbitMQ topology. (a) A branch publishes to the direct exchange `coffee.requests`, which routes by queue name to two quorum queues; workers compete for messages (prefetch = 1), send the reply through the default exchange to the client's exclusive reply queue, and acknowledge only after that. (b) A message that a worker rejects with requeue = false, or that has been delivered more than three times, is dead-lettered from its queue through `coffee.dlx` to the matching DLQ."),
    H2("Message Contract"),
    P("All messages are UTF-8 JSON. The photo is resized (long side ≤ 640 px), JPEG-compressed (quality 85) and " +
      "base64-encoded, so a request is only tens of kilobytes. A recognition request (`face.recognize`) carries " +
      "`request_id` (also the `correlation_id`), `branch_id`, `captured_at`, `auto_enroll`, an optional `full_name` and " +
      "`image_b64`; `reply_to` names the reply queue. A response carries `request_id`, `status`, `customer`, " +
      "`last_order`, `frequent_items` (up to three most-ordered items of a known customer), `similarity`, `latency_ms`, " +
      "`faces_detected`, `message`, `quality` (sharpness and face size), " +
      "`duplicate` and `worker` (the replying worker); an order reply carries `order_id` and `total_amount` instead of the customer fields. " +
      "{tab:status} lists the status codes. Every message is validated by the worker (types, required fields, valid " +
      "base64, quantity in 1–99); an invalid message raises `MessageError` and goes to the DLQ without crashing the worker."),
    TAB("status", "Response status codes", [0.38, 0.62], ["l", "l"], ["`status`", "Meaning"], [
      ["`RECOGNIZED`", "Known customer; with `customer`, `last_order`, `frequent_items`"],
      ["`NEW_CUSTOMER`", "New customer, enrolled; `customer` holds the new record"],
      ["`UNKNOWN`", "Not recognized and `auto_enroll=false`"],
      ["`NO_FACE`", "No face, face too small, or image too blurry"],
      ["`MULTIPLE_FACES`", "Several faces (policy `reject`)"],
      ["`ERROR`", "Processing error or invalid message"],
      ["`ORDER_SAVED`, `CUSTOMER_UPDATED`", "Replies of the order and update flows"],
    ]),
    H2("Data Model"),
    P("*MySQL* holds `branches`, `customers`, `customer_photos`, `orders`, `order_items` and `recognition_logs`. An index " +
      "on `orders(customer_id, ordered_at DESC)` serves the “latest order” lookup. `orders.request_id` is UNIQUE so " +
      "that order creation is idempotent, and `recognition_logs.request_id` is UNIQUE for idempotency and doubles as " +
      "measurement data. `recognition_logs.customer_id` deliberately has no foreign key, so log rows survive when a " +
      "customer is removed by compensation. Photos are stored as files (a relative path in `customer_photos.file_path`), " +
      "not as BLOBs, to keep the database small."),
    P("*PostgreSQL* holds `face_embeddings(embedding_id, customer_id, embedding vector(512), created_at)` with an HNSW " +
      "index (`vector_cosine_ops`). `customer_id` is a logical key into MySQL; a real foreign key is impossible across " +
      "two databases. Recognition runs a nearest-neighbor query:"),
    CODE("SELECT customer_id,\n       1 - (embedding <=> q) AS similarity\nFROM face_embeddings\nORDER BY embedding <=> q LIMIT 1;"),
    P("Each customer keeps at most five embeddings. When a face is recognized with confidence but is different enough " +
      "from the stored ones (similarity between 0.55 and 0.92), the worker adds it as a new embedding, which makes matching " +
      "more robust to lighting and pose."),
    H2("Processing Flows"),
    P("*Flow A (known customer).* The client resizes and compresses the photo and publishes it to `face.requests` with " +
      "`reply_to` and `correlation_id`. An idle worker receives it (prefetch = 1), looks up `recognition_logs` by " +
      "`request_id`, detects the face with SCRFD, aligns it, extracts an L2-normalized ArcFace embedding and asks pgvector " +
      "for the nearest customer by cosine similarity. If the similarity reaches the threshold *τ*, the worker fetches the " +
      "profile and latest order from MySQL, writes the log, publishes the reply and only then acknowledges the request."),
    P("*Flow B (new customer).* If the similarity is below *τ*, the worker enters a critical section (Section V-F), " +
      "re-checks, creates the customer in MySQL, stores the photo, writes the embedding to pgvector and replies " +
      "`NEW_CUSTOMER`. *Flow C (order).* The client sends `order.create` to `orders`; the worker writes `orders` and " +
      "`order_items` in one MySQL transaction, replies and acknowledges. {fig:flow} shows the message sequence of flows A and B."),
    FIG("flow", "seq_flow.png", "Recognition flow. Steps 1–5 are common; the alt fragment holds the known-customer branch (A) and the new-customer branch (B, under the advisory lock, compensated on failure); the photo file of step 8 goes to the shared photo store and its path to MySQL. The worker then writes the log, publishes the reply to the client's reply queue on the broker and only then acknowledges (steps 10–13)."),

    // ============================================================ V
    H1("Distributed-Systems Mechanisms"),
    P("Each subsection states the problem, the mechanism, its link to theory and how it is verified."),
    H2("Scalability"),
    P("Several workers consume `face.requests` as competing consumers. With `prefetch = 1` a worker holds at most one " +
      "unacknowledged message, so work is shared in proportion to speed: fast workers take more and a busy worker is " +
      "given nothing extra (dynamic load balancing without a separate balancer). Workers are stateless, with all state " +
      "in the databases, so capacity changes with `docker compose up -d --scale worker=N` and no code change. Each " +
      "request is a coarse-grained unit of work (hundreds of milliseconds) with little interaction, which parallelizes " +
      "well. The remaining centralized components are the broker and the two databases (Section VIII)."),
    P("*Pitfall on a single machine.* By default ONNX Runtime starts as many compute threads as there are cores in " +
      "*every* worker, so *N* workers compete for all cores (thread oversubscription) and throughput does not grow. We " +
      "added `ORT_THREADS` (0 = automatic), which caps the intra-op threads of each worker, and choose " +
      "workers × `ORT_THREADS` ≤ logical cores. InsightFace does not forward session options, so session creation is " +
      "wrapped. The effect is measured in Section VII-C."),
    H2("Load Leveling"),
    P("A durable queue is a buffer. When three branches send at once, requests queue up instead of being rejected: " +
      "clients see longer waits (higher p95 latency) rather than failures, and the queue drains to zero when the load " +
      "subsides. This is the queue-based load-leveling pattern; {fig:queue} shows a measurement (Section VII-C)."),
    H2("Fault Tolerance"),
    P("Request messages are persistent (`delivery_mode = 2`) while replies are transient, queues are quorum queues, and acknowledgements are manual and sent *after* the reply. If a " +
      "worker dies before acknowledging, the broker detects the closed connection, requeues the message and delivers it " +
      "to another worker ({fig:crash}); no request is lost (at-least-once delivery). Workers also shut down gracefully on " +
      "SIGTERM: they stop taking new messages and finish the current one."),
    FIG("crash", "seq_crash.png", "Worker crash: Worker 1 dies after writing log(R) but before replying and acknowledging; RabbitMQ sees the connection close and puts R back in the queue; Worker 2 finds log(R) and replays the stored result through the reply queue (duplicate = true), so no second customer is created."),
    P("As an illustration, if each worker is up with probability *p* and " +
      "failures are independent, at least one of *N* workers is available with probability"),
    EQ("avail", "*A*(*N*) = 1 − (1 − *p*)^{*N*}"),
    P("which for *p* = 0.95 gives 0.95, 0.9975 and 0.999875 for *N* = 1, 2, 3. This is a theoretical illustration only " +
      "(independence assumed, broker and databases ignored); real reliability is bounded by the weakest centralized component."),
    H2("Retries and Idempotency"),
    P("*The problem.* RabbitMQ delivers every request *at least once*, so the same request can reach the workers more " +
      "than once: (a) a branch gets no answer within 10 s and sends the request again; (b) a worker finishes the work but " +
      "crashes before acknowledging it, so the broker gives the request to another worker; (c) the reply is lost on its " +
      "way back and the branch sends again. If every copy were processed in full, one person could be registered twice " +
      "or one order saved twice."),
    P("*The mechanism.* Each request carries a `request_id` (a UUID) that the branch creates once and reuses for every " +
      "resend. A worker uses it in three steps:"),
    OL("Before doing anything else, it looks the `request_id` up in `recognition_logs`.",
       "If a row is found, the request has already been processed: the worker returns the stored result with " +
       "`duplicate = true`, without running the model and without creating any record.",
       "Otherwise it processes the request and writes its log row. The column `request_id` is UNIQUE, so if two copies " +
       "run at the same moment only one row can be inserted and the other copy is reported as a duplicate."),
    P("Orders use the same idea through the UNIQUE column `orders.request_id`: a repeated order returns the order already " +
      "saved. Updating a customer’s name or phone writes absolute values, so repeating it changes nothing. *Example:* " +
      "branch D sends request R; worker 1 saves the result and is killed before replying; RabbitMQ gives R to worker 2, " +
      "which finds R in the log and returns the saved result. The branch receives one answer and the database holds one " +
      "log row (measured in {tab:fault})."),
    P("*Relation to theory.* At-least-once delivery combined with duplicate detection gives *effectively-once* " +
      "processing. The idea is the at-most-once technique of {cite:liskov}, in which the server remembers the message " +
      "identifiers it has handled; we use random UUIDs instead of timestamps, so the branch machines need no synchronized " +
      "clocks. If a reply cannot be delivered because the branch has disconnected, the worker still acknowledges the " +
      "request; when the branch sends R again, it receives the result from the log."),
    H2("Consistency Across Two Databases"),
    P("MySQL and PostgreSQL share no transaction. Two-phase commit would be slow, prone to blocking and awkward across " +
      "heterogeneous systems, so we use a simple saga {cite:sagas}: ordered local writes with compensation."),
    OL("MySQL: insert the customer, write the photo file, insert the photo row.",
       "pgvector: insert the embedding (committed when the critical section ends).",
       "If step 2 fails (including at commit), delete the new customer and the photo file (`_compensate`)."),
    P("The order “MySQL first, pgvector second” is deliberate: an embedding, which makes the customer *recognizable*, " +
      "appears only once the customer and photo fully exist. Two kinds of orphans remain possible if compensation " +
      "itself fails or a worker dies between the steps: a customer without an embedding (never recognized) and an " +
      "embedding without a customer. They are handled in two layers: a worker deletes an orphan embedding when it meets " +
      "one during recognition, and `tools.reconcile` scans and repairs periodically (`--fix`). The `SagaTests` case " +
      "makes pgvector fail during enrollment: afterwards no customer and no photo remain, and after recovery the " +
      "redelivered message succeeds."),
    H2("Concurrency: Two Branches Meet the Same New Person"),
    P("The sequence “search → (none) → insert” is not atomic. Two workers handling two photos of the same new person " +
      "can both find nothing and both create a customer, giving two customers for one person. This is a race among " +
      "distributed processes; a process-local mutex such as Python’s `threading.Lock` cannot help. {tab:lock} compares two options."),
    TAB("lock", "Options against duplicate enrollment", [0.26, 0.34, 0.40], ["l", "l", "l"],
      ["Option", "Pros", "Cons"], [
        ["**A. Lock** (implemented)", "Never creates duplicates", "Serializes new-customer writes; PostgreSQL is a single point of failure for the lock"],
        ["B. Accept, merge later", "No bottleneck", "Transient duplicates; merge logic for orders and photos"],
      ]),
    P("Option A wraps “re-search → create customer → write embedding” in `pg_advisory_xact_lock`. It is centralized " +
      "mutual exclusion {cite:tanenbaum} with PostgreSQL as coordinator: simple and safe, with the textbook drawbacks " +
      "of a single point of failure and a bottleneck. This is acceptable here because (a) the critical section contains no model " +
      "inference, only one vector search and a few small writes, (b) it applies only to the new-customer path, while the returning-customer path, most of the load, " +
      "takes no lock, and (c) an advisory transaction lock is released automatically when the transaction ends or the " +
      "connection drops, so a crashed worker cannot hold it forever and no separate lease is needed."),
    P("After acquiring the lock the worker *re-checks* (double-checked locking): while it waited, another worker may " +
      "have enrolled the same person, in which case it returns `RECOGNIZED`. It also re-checks `recognition_logs` for " +
      "another copy of the same `request_id`. `ConcurrencyTests` run eight threaded “workers” over shared fake stores with " +
      "a simulated lock on different photos of one new person: exactly one customer, one `NEW_CUSTOMER`, seven " +
      "`RECOGNIZED`, and never more than one worker in the critical section. A control test without the lock creates " +
      "more than one customer, which shows that the lock is what prevents the race. The scenario was then repeated on the " +
      "real system ({tab:fault}): six requests carrying the same photo of a volunteer who was not yet enrolled were " +
      "published with `auto_enroll = true` to three workers; immediately afterwards the queue depth read 3, so the other " +
      "three requests had already been taken by the workers. The result was one `NEW_CUSTOMER` and five `RECOGNIZED`, " +
      "exactly one new customer row, and a clean `tools.reconcile`. This run had no control without the lock, so it shows " +
      "that no duplicate was created, not how long any worker waited for the lock."),
    H2("Security"),
    P("{tab:sec} summarizes the main threats and the countermeasures in place."),
    TAB("sec", "Threats and countermeasures", [0.36, 0.64], ["l", "l"], ["Threat", "Countermeasure"], [
      ["Eavesdropping, tampering", "Optional `amqps` overlay (port 5671; the client verifies the server with `ca.pem`); not exercised in the experiments, which ran inside the encrypted ZeroTier virtual LAN"],
      ["Forged or over-privileged branch", "One account per branch with least privilege: publish only to `coffee.requests`, configure and read only queues named `reply.*` (exclusive to the declaring connection); cannot read `face.requests`; the `branch_id` field inside a message is still self-declared (Section VIII)"],
      ["Unauthorized use of a branch terminal", "The branch web page asks for that branch’s RabbitMQ account; the password is checked by the broker itself, so the branch machine stores no password, and each machine is pinned to one branch, so another branch’s account is refused. Checked on the lab’s broker on 14 Nov for branch D: a wrong password and the account `branch_a` were both refused"],
      ["Monitoring that could alter data", "A read-only admin dashboard (`admin/`) listens only on 127.0.0.1 and is reached through an SSH tunnel; it has its own login with lockout, calls the RabbitMQ management API with GET requests only and opens read-only database sessions. On the lab’s MySQL 8.0.46 a write attempt from its session fails with error 1792, and PostgreSQL rejects one as well"],
      ["Malicious message", "Strict validation; bad messages go to the DLQ and never crash a worker"],
      ["Leaked biometric data", "Photo files with mode 0750/0640, path-traversal checks, worker runs as non-root"],
      ["Privacy", "Photos collected only with the subject’s consent; to be deleted after the course"],
      ["Spoofing with a photo or video", "Not handled (no liveness detection)"],
    ]),
    H2("Heterogeneity and Transparency"),
    P("AMQP with JSON is independent of language and operating system: a client written in Python, Java or Go can " +
      "connect as long as it follows the message contract. As for transparency {cite:coulouris}, a branch invokes " +
      "“recognize” without knowing where or how many workers there are (location and replication transparency); a " +
      "worker crash is hidden from the user (failure transparency); adding workers changes no application (scaling " +
      "transparency); and branches share data without interfering, thanks to the lock and idempotency (concurrency transparency)."),

    // ============================================================ VI
    H1("Implementation"),
    H2("Code Organization"),
    P("The Python code base is split into `common/` (message contract and validators in `messages.py`, topology " +
      "declaration in `rabbit.py`, environment-based configuration in `config.py`, so the same code runs on many " +
      "machines), `worker/` (`face_engine.py` for SCRFD + ArcFace and quality filters; `service.py` for business logic and " +
      "the distributed mechanisms: idempotency, lock, saga; `handler.py` for the ack/retry/DLQ policy; `worker.py` for " +
      "the consumer loop, reconnection and graceful shutdown; the MySQL, pgvector and photo repositories), `client/` " +
      "(`rpc_client.py`, a Tkinter GUI with webcam, a browser terminal served locally by `web.py` with a per-branch login, and a CLI), `admin/` (a read-only monitoring dashboard), " +
      "`tools/` (enroll, benchmark, threshold sweep, pairwise analysis, reconcile, chaos, a comparison of stored embeddings with the " +
      "labeled dataset, and a customer-merge tool that only previews unless given `--apply`), `scripts/b4/` (the four-branch run) " +
      "and `tests/` (129 unit tests over fake repositories)."),
    H2("Recognition Pipeline"),
    OL("Decode the JPEG to a BGR image.",
       "SCRFD detects the face and five landmarks; InsightFace aligns it to 112 × 112.",
       "ArcFace (`w600k_r50`) returns a 512-d embedding, L2-normalized. For unit vectors cosine similarity equals the dot " +
       "product, and `1 - (embedding <=> q)` in pgvector is exactly the cosine similarity.",
       "Quality gates (`evaluate_faces`): discard faces with detection score < 0.5 or shorter side < `MIN_FACE_PX` (60 px); an image whose face-crop sharpness (variance of the Laplacian, normalized to " +
       "112 × 112) is below 30 gives `NO_FACE` with a reason. Covered or blurred faces therefore never create junk " +
       "customers. With several faces the policy `largest` takes the face with the largest bounding box (usually the one nearest the camera), `reject` asks for a new photo."),
    P("`SIM_THRESHOLD` is tuned empirically (Section VII-B). `MIN_FACE_PX` and `MIN_BLUR_VAR` depend on the camera and lighting; " +
      "all 77 evaluation photos passed them with the defaults, so they were not tuned."),
    H2("Worker and Acknowledgement Policy"),
    P("Each worker is an independent process with `basic_qos(prefetch_count=1)`, `confirm_delivery()` and manual " +
      "acknowledgements. {tab:ack} is the complete policy for one message (`worker/handler.py`). The delivery count is " +
      "read from the `x-delivery-count` header of the quorum queue."),
    TAB("ack", "Acknowledgement policy", [0.4, 0.38, 0.22], ["l", "l", "l"],
      ["Outcome", "Request message", "Client gets"], [
        ["Success", "**reply first, ack after**", "result"],
        ["Malformed message", "`reject(requeue=false)` → DLQ", "`ERROR`"],
        ["Business error (e.g., unknown customer)", "ack (a retry is useless)", "`ERROR`"],
        ["Transient error, attempts left", "`nack(requeue=true)`", "(not yet)"],
        ["Transient error, `MAX_DELIVERIES` = 3 used up", "`reject` → DLQ", "`ERROR`"],
        ["Worker dies before ack", "broker redelivers", "result"],
      ]),
    H2("Client and Deployment"),
    P("`BranchRpcClient` creates its private reply queue and publishes with `correlation_id = request_id` and " +
      "`reply_to`, with publisher confirms and the `mandatory` flag so that it knows the broker accepted the message. " +
      "After a 10 s timeout it resends with the *same* `request_id`, for at most three attempts in total; after a lost connection it " +
      "reconnects with back-off. Permission (403) and missing-exchange (404) errors are reported with their cause " +
      "instead of waiting for a timeout. The Tkinter GUI performs all network calls in a background thread. On the server, " +
      "`scripts/quickstart.sh 3` starts the broker, both databases, three workers and the branch accounts. A branch machine " +
      "normally runs the browser terminal, `python -m client.web --branch D --host <server>`: the page asks for that branch’s " +
      "account before any request is sent. The GUI (`client.gui`) and the CLI remain available."),

    // ============================================================ VII
    H1("Experimental Evaluation"),
    H2("Setup"),
    P("The server is an Intel Core i5-11500B at 3.30 GHz (6 cores, 12 threads) with 7.4 GiB RAM running Ubuntu 22.04.5 LTS. " +
      "RabbitMQ, PostgreSQL, MySQL and all workers run under Docker Compose on this *same machine*, so the scaling " +
      "measurements are bounded by the CPU of a single machine. One branch client runs on a separate laptop (Windows with " +
      "Ubuntu 24.04 under WSL2) and reaches the server over a private ZeroTier virtual LAN created for this project. " +
      "All measurements below were taken before the worker began attaching the `frequent_items` lookup (one extra read-only " +
      "aggregate query per returning customer), so processing times for known customers may be slightly higher with the " +
      "current code; that effect was not re-measured. Cross-machine timings use only `recognition_logs.created_at`, written by " +
      "the single MySQL clock, never the `captured_at` stamped by a branch machine: in one session branch timestamps were off " +
      "by up to 6.85 s, far more than the delays of about 0.4 s that we measure."),
    P("As a multi-host check, that client connected to the server’s RabbitMQ with the least-privilege `branch_b` account " +
      "and sent a recognition request for a customer whose latest order had been placed at branch A. The reply was " +
      "*returning customer* together with that branch-A order; the RabbitMQ management UI showed the connection " +
      "`branch-B-laptop` from the laptop’s ZeroTier address, the worker-side processing times were 319–470 ms and the log row " +
      "records `branch_id = B`. This validates the distributed path and the state shared across branches; it is a functional " +
      "check, not an accuracy estimate. In a later rehearsal, a customer created and an order saved in the branch-A GUI were " +
      "recognized, with that order, in the branch-B GUI (similarity 1.000 only because the enrollment image was reused). " +
      "A live check with the Tkinter client and the server’s webcam (auto-enrollment off) recognized one author’s face as " +
      "the enrolled customer at similarity 0.724 in 302 ms, from a live webcam frame rather than a stored photo; it is a " +
      "single observation, not an accuracy estimate."),
    P("Dataset: 11 consenting classmates, 77 photos in total (6–14 per person). Seven people are " +
      "enrolled with two photos each, picked by eye before any measurement was run; their remaining " +
      "39 photos are the test probes of returning customers. The other four people (24 photos) are never enrolled and play " +
      "the strangers, to measure false acceptance. All 77 photos passed the quality gate, so none was discarded, and the " +
      "test photos were not filtered."),
    H2("Threshold Selection"),
    P("`tools.eval_threshold` sweeps the cosine threshold *τ* from 0.30 to 0.60 in steps of 0.02. *Correct* is a returning " +
      "customer recognized as themselves; *FRR* is a miss (a duplicate customer is created); *misidentified* is a customer " +
      "shown another customer’s order, the most serious error; *FAR* is a stranger accepted as an existing customer. " +
      "{tab:thr} gives the result on 39 returning-customer probes and 24 stranger probes (one returning-customer probe is worth 2.6 " +
      "percentage points and one stranger probe 4.2); consecutive thresholds with identical results are merged into one row. For every *τ* from 0.30 " +
      "to 0.46 all 39 returning customers are recognized as themselves and all 24 strangers are rejected, so all 63 " +
      "decisions (39 + 24) are correct. From *τ* = 0.48 one returning-customer probe, whose similarity lies between 0.46 and 0.48, is " +
      "missed (FRR 2.6%), and FRR grows to 7.7% at *τ* = 0.60. At no threshold in the range was a returning customer " +
      "matched to someone else or a stranger accepted, so the best similarity of every stranger to the gallery stays below " +
      "0.30."),
    P("Because the best-accuracy plateau spans 0.30–0.46, we apply the tie rule of the tool, which prefers the higher threshold " +
      "since showing another customer’s order is worse than creating a duplicate customer, and choose *τ* = 0.46. It is " +
      "deployed through `SIM_THRESHOLD`; the default in the code is 0.45, which gives identical results on this data. The " +
      "result is indicative only: the gallery has seven people, 0 of 24 strangers accepted bounds the FAR at no more than about 12.5% " +
      "(95% confidence, rule of three) and FAR will rise as the gallery grows, and the weakest genuine probe sits just " +
      "above the chosen threshold, so the optimum may shift with another camera or lighting."),
    P("*Pairwise check.* Comparing every photo with every other one (77 photos: 262 same-person and 2664 different-person " +
      "pairs) does not depend on how gallery and probes are split. Same-person similarity has minimum 0.257, 5th " +
      "percentile 0.440 and median 0.642; different-person similarity has median 0.095, 99th percentile 0.275 and " +
      "maximum 0.338, so no different-person pair reaches 0.34. The pairs share photos and are not independent, so these figures " +
      "describe the data and are not confidence intervals. However, 20 of the 262 same-person pairs (7.6%) fall below " +
      "*τ* = 0.46 (3.8% below 0.42). With an arbitrary single enrollment photo a customer is therefore missed more often " +
      "than the 0.0% of {tab:thr} at *τ* = 0.46 (2.6% at 0.48), which used two photos per person chosen in advance: in a live rehearsal a customer enrolled " +
      "from one photo was not recognized from another photo of themselves (similarity 0.335). Matching uses the closest " +
      "of up to five embeddings per customer, so enrolling more than one varied photo should lower the miss rate, and a miss " +
      "costs a duplicate record, not a wrong identity. We still keep *τ* = 0.46: in the same rehearsal a stranger scored " +
      "0.383 against a customer enrolled in the live database, above every different-person pair inside the dataset, so the safety margin " +
      "matters more as the customer base grows."),
    P("*A duplicate observed in the live database.* One volunteer was enrolled twice. When the second record was created, " +
      "the best similarity to the existing record of the same person was 0.447, just below *τ* = 0.46, so the worker created a " +
      "new customer as designed. A later embedding raised the best cross-pair similarity to 0.498, and `tools.reconcile` " +
      "flagged the pair. Comparing every stored embedding with the labeled dataset (`tools/db_vs_dataset.py`) showed that both " +
      "records belonged to the same person; after a backup, `tools.merge_customers` merged them on 8 Nov (photos, orders and " +
      "embeddings moved to one record) and `tools.reconcile` was clean again. This single case illustrates the 7.6% figure " +
      "above: a miss costs a duplicate record, which the audit can find and repair, not a wrong identity."),
    TAB("thr", "Threshold sweep: 39 returning-customer and 24 stranger probes (identical rows merged)", [0.22, 0.17, 0.14, 0.25, 0.22], ["c", "c", "c", "c", "c"],
      ["*τ*", "Correct", "FRR", "Misidentified", "FAR"], [
        ["0.30–0.46", "100.0%", "0.0%", "0.0%", "0.0%"],
        ["0.48–0.54", "97.4%", "2.6%", "0.0%", "0.0%"],
        ["0.56–0.58", "94.9%", "5.1%", "0.0%", "0.0%"],
        ["0.60", "92.3%", "7.7%", "0.0%", "0.0%"],
      ]),
    H2("Scalability"),
    P("All runs use the same machine, the same photo of an enrolled customer, `auto_enroll=false` (no new customers) and " +
      "`DEMO_DELAY_MS=0`. Two independent measurements are reported; each configuration was run once, so no variance is shown."),
    P("*Benchmark.* `tools.benchmark` sends 100 requests from 12 parallel clients with `ORT_THREADS=2` ({tab:bench}, {fig:bench})."),
    TAB("bench", "Benchmark: 100 requests, 12 clients", [0.14, 0.17, 0.15, 0.15, 0.39], ["c", "c", "c", "c", "c"],
      ["Workers", "req/s", "p50 (ms)", "p95 (ms)", "Split across workers"], [
        ["1", "3.45", "3456", "3519", "100"],
        ["2", "6.34", "1854", "1937", "50 / 50"],
        ["4", "8.26", "1416", "1571", "26 / 24 / 25 / 25"],
      ]),
    FIG("bench", "bench.png", "Benchmark results: throughput (left) and end-to-end latency (right) versus workers."),
    P("All runs completed 100/100 requests with no errors or resends, and the work was split almost evenly (competing " +
      "consumers with `prefetch = 1`). Throughput rises 1.84× with two workers (92% efficiency) and 2.39× with four (60%): " +
      "it grows, but sublinearly. The p50 latency falls from 3.5 s to 1.4 s. These values are large because 12 clients " +
      "submit continuously, so latency is dominated by queueing, as Little’s law {cite:little} predicts: *W* = *L*/*λ* with *L* = 12 requests in the system and *λ* the throughput: " +
      "12/3.45 = 3.48 s, 12/6.34 = 1.89 s and 12/8.26 = 1.45 s. More workers shorten the waiting time, not the service time of one request."),
    FIG("lat", "latency_req.png", "Latency of every benchmark request in submission order (12 clients; the runs of {tab:bench})."),
    P("{fig:lat} shows each request. The first 12 requests, sent almost together by the 12 clients, queue behind one another, so " +
      "latency grows by about one service time per request ahead in the queue: the burst is absorbed, not rejected. " +
      "Afterwards latency stays flat at about 3.5 s, 1.9 s and 1.4 s for one, two and four workers, the waiting time that Little’s law gives."),
    P("*Drain test.* `scripts/measure_drain.sh` floods 200 requests with `tools.chaos flood`. Every finished request " +
      "leaves a row in MySQL `recognition_logs`, so “done” means 200 new rows and the time is taken from `created_at`, " +
      "independent of RabbitMQ’s delayed queue statistics; *req/s* is 200 divided by the time between the first and the last of these rows. We measured twice: with default ONNX Runtime threads and with " +
      "`ORT_THREADS=2` ({tab:drain}, {fig:scale}). *ms/req* is the mean processing time per request measured inside the worker " +
      "(`recognition_logs.latency_ms`); speedup is relative to one worker with the same thread setting, and efficiency is the " +
      "speedup divided by the number of workers (both computed from the unrounded throughput)."),
    TAB("drain", "Drain test: 200 requests", [0.15, 0.25, 0.12, 0.19, 0.15, 0.14], ["c", "l", "c", "c", "c", "c"],
      ["Workers", "ONNX threads", "req/s", "ms/req", "Speedup", "Eff."], [
        ["1", "default", "6.72", "147", "1.00×", "100%"],
        ["2", "default", "6.50", "305", "0.97×", "48%"],
        ["3", "default", "5.49", "549", "0.82×", "27%"],
        ["1", "2 per worker", "3.48", "285", "1.00×", "100%"],
        ["2", "2 per worker", "6.16", "324", "1.77×", "88%"],
        ["4", "2 per worker", "8.36", "479", "2.40×", "60%"],
        ["6", "2 per worker", "9.06", "669", "2.60×", "43%"],
      ]),
    FIG("scale", "scaling.png", "Throughput versus workers: default ONNX threads against two threads per worker. Markers are measured points; the dashed line is 3.48 req/s × *N*, one tuned worker scaled ideally."),
    OL("*The default configuration does not scale on one machine.* Throughput goes from 6.72 to 6.50 to 5.49 req/s for 1, 2 and 3 " +
       "workers while the time per request grows from 147 to 549 ms. ONNX Runtime sizes its thread pool by the core count in *every* worker, " +
       "so *N* workers fight for the same CPUs (thread oversubscription) while the total resources stay constant.",
       "*Capping threads per worker* (`ORT_THREADS=2`, workers × 2 ≤ 12) makes added workers effective: 3.48, 6.16, 8.36 and " +
       "9.06 req/s with 1, 2, 4 and 6 workers (1.77×, 2.40×, 2.60×).",
       "*Limits of the measurement.* The machine has only six physical cores and also runs MySQL, PostgreSQL, RabbitMQ and " +
       "the load generator; one default worker (6.72 req/s) already uses most of the CPU. The ceiling is therefore about " +
       "9 req/s, and the best configuration is only about 35% faster than the default one. This is a limit of shared " +
       "resources on one machine, not of the design: the work stayed evenly split (200 for one worker, 100/100 for two, 50/50/49/51 for four " +
       "and 34/33/34/33/33/33 for six). Scaling further needs more machines, each with its own CPU; a multi-machine measurement " +
       "has not been done (Section VIII)."),
    P("*Queue depth during a burst.* To see load leveling directly, `tools.chaos watch` samples the number of messages waiting " +
      "in `face.requests` every 0.5 s while `tools.chaos flood` publishes 200 requests at once ({fig:queue}; `ORT_THREADS=2`). The queue " +
      "jumps from 0 to about 200 within one sampling interval and then drains almost linearly, so the service rate is constant and " +
      "the broker accepts the whole burst: from 198 to 0 in 57.6 s with one worker (3.4 requests/s) and from 197 to 0 in 25.1 s " +
      "with three (7.9 requests/s). These rates are consistent with the drain test of {tab:drain} (3.48 req/s with one worker; " +
      "6.16 and 8.36 with two and four). The count excludes messages that workers hold unacknowledged, and each configuration " +
      "was run once."),
    FIG("queue", "queue_depth.png", "Messages waiting in `face.requests` after a burst of 200 requests, one worker versus three (sampled every 0.5 s; time zero is the last sample before the burst)."),
    H2("Fault-Tolerance Experiments"),
    P("{tab:fault} lists each fault-injection scenario, how it was triggered and the observed outcome."),
    TAB("fault", "Fault-injection results", [0.34, 0.66], ["l", "l"], ["Scenario (method)", "Outcome"], [
      ["Kill a worker mid-request (`DEMO_DELAY_MS=4000`, `demo_kill_worker.sh`)",
       "**Passed (two runs).** In the second, fully logged run (`results/kill_demo_*.txt`), `worker-8` wrote the log row for request `593d6f0a…`, entered the injected 4 s pause and was killed; `worker-7` received the same request about 0.4 s later and, after its own injected 4 s pause, replied from the stored result (`duplicate = true`, no second face analysis, 16 ms). `recognition_logs` holds exactly one row for the request, both main queues are empty, the DLQ message counts are unchanged and `tools.reconcile` is clean. An earlier run (request `96f70b22…`) gave the same outcome (redelivery after about 0.3 s, replay 17 ms, no duplicate rows), but its raw logs are not included. Both runs use the injected pause and test one failure point: after the database write, before reply and acknowledgement"],
      ["Client timeout and resend (same `request_id` twice)", "**Passed.** Second reply has `duplicate = true`; no new customer"],
      ["Malformed messages (`tools.chaos poison`)", "**Passed.** Three messages reached `face.requests.dlq` (not JSON; missing `image_b64`; invalid `image_b64`); `orders.dlq` stayed empty"],
      ["Request bursts (`tools.chaos flood`)", "**Passed.** A burst of 300 requests made the queue reach 296 messages. In a later burst of 200 ({fig:queue}) the queue went from 197 to 0 in 25 s with three workers; in an earlier rehearsal the unacknowledged count stayed at three (3 workers × `prefetch = 1`) and the customer count did not change"],
      ["pgvector fails during enrollment (`SagaTests`)", "**Passed.** No orphan data"],
      ["Several workers meet one new person (`ConcurrencyTests`)", "**Passed** 3/3, including the control test without the lock"],
      ["Six copies of one new person’s photo to three workers (`tools.chaos flood --enroll`, real PostgreSQL)", "**Passed.** `recognition_logs` shows 1 `NEW_CUSTOMER` and 5 `RECOGNIZED`; the customer table grew by exactly one row (6 to 7); `tools.reconcile` clean"],
      ["Cross-database audit after all runs (`tools.reconcile`)", "**Passed.** No orphan customers or embeddings, no duplicate pairs"],
    ], { size: 15 }),
    H2("Automated Tests"),
    P("All 129 unit tests pass (`python -m unittest discover -s tests -t .`). They cover: new customer then returning " +
      "customer at another branch with order; `NO_FACE` and `MULTIPLE_FACES` creating no junk customers; idempotency of " +
      "recognition and orders; redelivery after a worker crash; the duplicate-prevention lock with a control; saga " +
      "compensation; the handler’s ack/retry/DLQ rules; message validation; blur filtering on synthetic images; and " +
      "percentile and threshold-sweep computation on synthetic embeddings with known answers; the ranking of a customer’s most-ordered items; the HTTP layer of the browser terminal (input validation, error mapping, rejection of cross-origin requests) and its per-branch login and sessions; the read-only admin dashboard (login, lockout, request rules and the summary computations); the merge plan of the customer-merge tool; and the order of reply and acknowledgement, which fails if a handler acknowledges before replying. The tests use in-memory " +
      "fake repositories and so verify the *logic* of the mechanisms; verification on real RabbitMQ, MySQL and " +
      "PostgreSQL is the experiments above."),

    // ============================================================ VIII
    H1("Limitations and Future Work"),
    P("*Limitations.*"),
    UL("**Single points of failure:** one broker and one instance of each database in this configuration.",
       "**Scaling measured on one machine only** (6 physical cores), so it is capped by shared CPU; workers on several machines were not measured.",
       "**Shared photo directory:** workers on several machines need NFS or object storage (MinIO/S3); `PhotoStore` is the only module to replace.",
       "**The lock depends on PostgreSQL** and would bottleneck at a very high rate of new customers.",
       "**No liveness detection:** a printed photo or a phone screen can fool the system.",
       "**TLS not exercised:** the `amqps` overlay and certificate script exist but were not run on a real network; the multi-host test used plain AMQP inside the ZeroTier virtual LAN.",
       "**Multi-host evidence comes from one remote branch at a time:** the checks in Section VII used one laptop; a run in which four branches on four machines send at once is prepared (`scripts/b4/`), but its results are not included in this version.",
       "**Coarse reply-queue permissions:** every branch account may configure and read any queue named `reply.*`; a per-branch name pattern would tighten this.",
       "**Self-declared branch in messages:** the broker authenticates each branch account, but the `branch_id` field inside a message is written by the sender and not yet checked against that account; the admin dashboard shows both, so a mismatch is visible but not blocked.",
       "**Workers share the RabbitMQ administrator account** instead of a least-privilege worker account.",
       "**Default passwords** are still used in the lab deployment and must be changed before any wider exposure.",
       "**The admin dashboard uses plain HTTP;** it listens only on the server’s loopback address and is reached through an SSH tunnel.",
       "**Accuracy** depends on camera quality and lighting and was measured on only 11 volunteers (63 probes); thresholds need calibration per deployment. The pretrained model is non-commercial.",
       "**The clients are laptop applications** (a Tkinter GUI and a browser terminal served on the branch machine itself), not a real mobile app; an API gateway (e.g., FastAPI) would let phones capture through the web."),
    P("*Future work.* (1) A three-node RabbitMQ cluster with replicated quorum queues and replicas for PostgreSQL and " +
      "MySQL; (2) object storage and workers on several machines; (3) automatic detection and merging of duplicate customers " +
      "(option B) next to the lock, building on `tools.reconcile` and the manual `tools.merge_customers`; (4) liveness detection and a retention and deletion policy; " +
      "(5) autoscaling on queue depth (e.g., KEDA) with metrics and alerts (Prometheus, Grafana); (6) a model with a " +
      "commercial licence for a real deployment."),

    // ============================================================ IX
    H1("Conclusion"),
    P("The system meets the assignment: InsightFace on ONNX Runtime for recognition, RabbitMQ for communication, " +
      "PostgreSQL with pgvector for vectors, and MySQL for profiles, photos and orders. More importantly, it realizes and " +
      "verifies the core distributed-systems mechanisms: scale-out with competing consumers, fault tolerance with manual " +
      "acknowledgements and redelivery, idempotency, a saga for two-database consistency, a lock against races, a " +
      "dead-letter queue, least-privilege branch accounts with a per-branch login, and a read-only monitoring dashboard (TLS is provided as an optional overlay that was not exercised). The experiments also give a practical lesson: scaling " +
      "out on one machine requires capping per-worker threads; with the cap, six workers reach 2.6× the throughput of " +
      "one, whereas without it extra workers make things worse. Killing a worker in the middle of a request loses " +
      "nothing and duplicates nothing. Recognition accuracy, by contrast, was checked only on a small dataset and is indicative."),
  ],
};
