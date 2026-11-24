const { H1, H2, H3, P, UL, OL, CODE, EQ, FIG, TAB } = require("./lib");

module.exports = {
  labels: { lang: "vi-VN", abstract: "Tóm tắt", keywords: "Từ khóa", references: "Tài liệu tham khảo",
            figIn: "Hình", figCap: "Hình", tabIn: "Bảng", tabCap: "BẢNG" },
  meta: {
    title: "Nhận diện khuôn mặt phân tán cho chuỗi quán cà phê nhiều chi nhánh trên nền message broker",
    authors: [
      { name: "Lê Chí Tâm", id: "2302220" },
      { name: "Triệu Minh Thuận", id: "2302059" },
      { name: "Nguyễn Hạo Khang", id: "2302319" },
    ],
    idLabel: "MSSV",
    courseLine: "CS401V – Hệ phân tán, TTU",
    note: "Báo cáo môn học – Bài thực hành 2, Fall 2026",
  },
  abstract:
    "Bài báo trình bày một hệ thống nhận diện khuôn mặt cho chuỗi quán cà phê nhiều chi nhánh, được xây dựng như một hệ " +
    "phân tán. Khi khách đến, chi nhánh chụp ảnh và gửi về máy chủ trung tâm qua message broker RabbitMQ. Nhiều worker " +
    "giống hệt nhau cùng chia nhau xử lý các yêu cầu. Mỗi worker dùng InsightFace để tìm khuôn mặt, chuyển khuôn mặt thành " +
    "một vector 512 chiều, rồi tìm vector gần nhất đã lưu trong PostgreSQL (pgvector). Nếu là khách quen, hệ thống trả về " +
    "đơn gần nhất của khách từ MySQL; nếu là người mới, hệ thống đăng ký khách mới. Trọng tâm của đồ án là các vấn đề của " +
    "hệ phân tán chứ không phải độ chính xác nhận diện: thêm worker để tăng năng lực xử lý (competing consumers); giữ các " +
    "yêu cầu dồn dập trong hàng đợi; chuyển yêu cầu của worker bị chết sang worker khác (ack thủ công); xử lý một yêu cầu " +
    "gửi lặp lại đúng một lần (idempotency); giữ hai CSDL nhất quán dù không có transaction chung (saga); không để hai " +
    "worker cùng tạo một khách (advisory lock); và cấp cho mỗi chi nhánh một tài khoản quyền tối thiểu. Trên một máy 6 " +
    "nhân, cấu hình luồng mặc định của ONNX Runtime khiến hệ thống không mở rộng được: thông lượng giảm từ 6,72 xuống 5,49 " +
    "yêu cầu mỗi giây khi tăng từ một lên ba worker. Khi giới hạn mỗi worker hai luồng, thông lượng với 2, 4 và 6 worker " +
    "lần lượt gấp 1,77, 2,40 và 2,60 lần một worker. Giết một worker đang xử lý dở không làm mất yêu cầu nào và không tạo " +
    "bản ghi trùng.",
  keywords:
    "hệ phân tán, hàng đợi message, RabbitMQ, nhận diện khuôn mặt, khả năng mở rộng, chịu lỗi, idempotency, saga, pgvector",

  blocks: [
    // ============================================================ I
    H1("Giới thiệu"),
    P("Một chuỗi quán cà phê có nhiều chi nhánh muốn nhận ra khách quen ngay khi họ bước vào và gợi lại đơn gần nhất của " +
      "họ. Đề bài yêu cầu một hệ phân tán trong đó (i) máy chủ nhận diện khuôn mặt và, với khách đã nhận ra, gửi đơn gần " +
      "nhất về chi nhánh để xác nhận, còn với khuôn mặt lạ thì báo cho chi nhánh và lưu người đó như khách mới; (ii) mỗi " +
      "chi nhánh chạy một ứng dụng client chụp ảnh và gửi yêu cầu nhận diện; và (iii) dùng model nhận diện nguồn mở, " +
      "socket hoặc message broker nguồn mở và CSDL tự thiết kế."),
    P("Ngoài một bộ nhận diện chạy đúng, mục tiêu của chúng tôi là thể hiện các đặc trưng cốt lõi của hệ phân tán: nhiều " +
      "chi nhánh gửi yêu cầu đồng thời, mở rộng bằng cách thêm worker, chịu được worker bị chết, và giữ dữ liệu nhất quán " +
      "giữa hai CSDL không chung transaction. Các đóng góp gồm:"),
    UL("kiến trúc dựa trên broker, trong đó các worker không giữ trạng thái nhận yêu cầu theo kiểu competing consumers và " +
       "client có ngữ nghĩa RPC nhờ `correlation_id` và `reply_to` (Mục III và IV);",
       "tập cơ chế cho các vấn đề phân tán nêu trên: ack thủ công sau khi reply, quorum queue kèm dead-letter queue, " +
       "idempotency theo request ID, saga giữa MySQL và pgvector, và loại trừ tương hỗ bằng advisory lock (Mục V);",
       "nghiên cứu thực nghiệm trên hệ thống thật, gồm một cạm bẫy thread oversubscription làm hỏng việc mở rộng trên một " +
       "máy, và kết quả tiêm lỗi (Mục VII)."),
    P("Mục II nêu yêu cầu; Mục III–VI mô tả kiến trúc, thiết kế, cơ chế và cài đặt; Mục VII báo cáo thực nghiệm; Mục VIII–IX " +
      "bàn về hạn chế và kết luận."),

    // ============================================================ II
    H1("Yêu cầu và mục tiêu thiết kế"),
    P("{tab:req} liệt kê yêu cầu chức năng (F) và phi chức năng (N) cùng cách kiểm chứng. Các yêu cầu phi chức năng là " +
      "trọng tâm của đồ án."),
    TAB("req", "Yêu cầu và cách kiểm chứng", [0.09, 0.57, 0.34], ["l", "l", "l"],
      ["Mã", "Yêu cầu", "Kiểm chứng bằng"], [
        ["F1", "Client chi nhánh chụp ảnh và gửi yêu cầu", "GUI / CLI"],
        ["F2", "Nhận ra khách quen, trả đơn gần nhất", "Luồng A"],
        ["F3", "Khách mới: báo và lưu cho lần sau", "Luồng B"],
        ["F4", "Lưu đơn hàng của khách", "Luồng C"],
        ["F5", "Xử lý ảnh không mặt, mờ, nhiều mặt", "`NO_FACE`"],
        ["F6", "Cập nhật tên, số điện thoại", "`customer.update`"],
        ["N1", "**Mở rộng:** thêm worker, thông lượng tăng", "req/s, 1–6 worker"],
        ["N2", "**Chịu tải đột biến** không lỗi", "độ sâu queue"],
        ["N3", "**Chịu lỗi:** worker chết không mất request", "request mất = 0"],
        ["N4", "**Gửi lại an toàn:** không trùng khách, đơn", "số trùng = 0"],
        ["N5", "**Nhất quán** giữa MySQL và pgvector", "bản ghi mồ côi"],
        ["N6", "**Bảo mật:** xác thực, phân quyền, mã hoá", "Tài khoản chi nhánh và đăng nhập; TLS chưa thử"],
        ["N7", "**Không đồng nhất:** client độc lập ngôn ngữ", "AMQP + JSON"],
      ]),
    P("Ràng buộc: dùng model nguồn mở, broker nguồn mở và lược đồ CSDL tự thiết kế; demo trực tiếp trên nhiều máy thật. " +
      "Khuôn mặt là dữ liệu sinh trắc học nhạy cảm nên chỉ thu ảnh khi có sự đồng ý của người chụp và thư mục ảnh được " +
      "hạn chế quyền truy cập."),

    // ============================================================ III
    H1("Kiến trúc hệ thống"),
    H2("Tổng quan"),
    P("Hệ thống theo mô hình nhiều client/nhiều server xây trên middleware hướng thông điệp, có thể xem là chuỗi nhiều " +
      "tầng: client chi nhánh → broker → worker → hai CSDL ({fig:arch}). Worker vừa là server của các chi nhánh, vừa là " +
      "client của các CSDL {cite:coulouris,tanenbaum}. Theo phân lớp platform / middleware / application " +
      "{cite:coulouris}, ta xem platform là hệ điều hành, TCP/IP và Docker; middleware là RabbitMQ {cite:rabbitmq}, che giấu chi " +
      "tiết mạng và cho RPC bất đồng bộ qua `correlation_id` và `reply_to`; application là client chi nhánh và " +
      "recognition worker."),
    FIG("arch", "architecture.png", "Kiến trúc hệ thống. Bốn client chi nhánh chỉ nói chuyện với broker. Các worker giống nhau, không giữ trạng thái, chia nhau xử lý yêu cầu, và worker nào cũng đọc, ghi cùng hai CSDL và kho ảnh."),
    H2("Vì sao dùng message broker thay cho socket thuần"),
    P("Với socket thuần phải tự cài hàng đợi chịu tải đột biến, ack kèm giao lại, chia việc giữa các worker, dead-letter " +
      "và giám sát; phần lớn thời gian đồ án sẽ dành cho hạ tầng thay vì các vấn đề phân tán thật sự. RabbitMQ có sẵn " +
      "hàng đợi bền vững (tải đột biến), ack thủ công (giao lại khi lỗi), competing consumers với `prefetch` (cân bằng " +
      "tải), dead-letter exchange (message hỏng), giao diện quản lý và client AMQP cho nhiều ngôn ngữ. Broker còn tách " +
      "rời client và worker về không gian và thời gian: chi nhánh không cần biết có bao nhiêu worker, hay worker có đang " +
      "chạy ngay lúc đó không."),
    H2("Lựa chọn công nghệ"),
    P("Broker là RabbitMQ 3.13 với quorum queue. Nhận diện dùng InsightFace `buffalo_l` {cite:insightface}: bộ phát hiện " +
      "SCRFD {cite:scrfd} và embedding ArcFace 512 chiều {cite:arcface}, chạy bằng ONNX Runtime trên CPU {cite:onnx}. " +
      "Vector khuôn mặt nằm trong PostgreSQL 16 với pgvector và chỉ mục HNSW cosine {cite:pgvector,hnsw}; khách, ảnh, đơn " +
      "hàng và nhật ký nhận diện nằm trong MySQL 8. Worker và client viết bằng Python, triển khai bằng Docker Compose " +
      "(`--scale worker=N`). Các model pretrained `buffalo_l` chỉ được cấp phép cho nghiên cứu phi thương mại; đồ án này " +
      "phục vụ học tập, còn triển khai thương mại phải dùng model hoặc giấy phép khác."),
    P("*Vì sao hai CSDL?* pgvector chuyên cho tìm kiếm tương tự; MySQL mạnh về dữ liệu quan hệ và giao dịch. Kho vector " +
      "chỉ trả về `customer_id`, mọi thông tin khác nằm ở MySQL. Cái giá là mất transaction chung, được xử lý ở Mục V-E."),

    // ============================================================ IV
    H1("Thiết kế chi tiết"),
    H2("Topology của RabbitMQ"),
    P("Client chỉ được publish vào direct exchange `coffee.requests`, exchange này định tuyến tới hai quorum queue bền " +
      "vững: `face.requests` (nhận diện) và `orders` (lệnh ghi: tạo đơn, cập nhật khách). Cả hai queue dead-letter sang " +
      "`coffee.dlx`, nơi chuyển tiếp vào `face.requests.dlq` và `orders.dlq` cho message bị từ chối hoặc quá số lần giao. " +
      "Mỗi client sở hữu một queue trả lời exclusive, auto-delete tên `reply.<chi nhánh>.<uuid>` để nhận kết quả, đối " +
      "chiếu bằng `correlation_id`. {fig:mq} vẽ topology này cùng những việc tài khoản chi nhánh được phép làm."),
    FIG("mq", "rabbitmq_topology.png", "Topology RabbitMQ. (a) Chi nhánh publish vào direct exchange `coffee.requests`, exchange định tuyến theo tên queue tới hai quorum queue; các worker cạnh tranh nhận message (prefetch = 1), gửi reply qua default exchange vào queue trả lời exclusive của client và chỉ ack sau bước đó. (b) Message bị worker từ chối với requeue = false, hoặc đã giao quá ba lần, bị dead-letter từ queue của nó qua `coffee.dlx` sang DLQ tương ứng."),
    H2("Hợp đồng message"),
    P("Mọi message là JSON UTF-8. Ảnh được resize (cạnh dài ≤ 640 px), nén JPEG (chất lượng 85) rồi mã hoá base64 nên một " +
      "yêu cầu chỉ vài chục kilobyte. Yêu cầu nhận diện (`face.recognize`) gồm `request_id` (cũng là `correlation_id`), " +
      "`branch_id`, `captured_at`, `auto_enroll`, `full_name` (tuỳ chọn) và `image_b64`; `reply_to` chỉ tên queue trả " +
      "lời. Phản hồi gồm `request_id`, `status`, `customer`, `last_order`, `frequent_items` (tối đa ba món khách quen gọi nhiều nhất), `similarity`, `latency_ms`, " +
      "`faces_detected`, `message`, `quality` (độ nét và kích thước mặt), `duplicate` và `worker` (worker đã trả lời); phản hồi đơn hàng có `order_id` và `total_amount` thay cho các trường về khách. {tab:status} liệt kê các mã trạng thái. Mọi message đều được " +
      "worker kiểm tra (kiểu, trường bắt buộc, base64 hợp lệ, số lượng từ 1 đến 99); message sai sinh " +
      "`MessageError` và chuyển vào DLQ mà không làm worker sập."),
    TAB("status", "Các mã trạng thái phản hồi", [0.38, 0.62], ["l", "l"], ["`status`", "Ý nghĩa"], [
      ["`RECOGNIZED`", "Khách quen; kèm `customer`, `last_order`, `frequent_items`"],
      ["`NEW_CUSTOMER`", "Khách mới, đã lưu; `customer` chứa hồ sơ mới"],
      ["`UNKNOWN`", "Chưa nhận ra và `auto_enroll=false`"],
      ["`NO_FACE`", "Không có mặt, mặt quá nhỏ hoặc ảnh quá mờ"],
      ["`MULTIPLE_FACES`", "Nhiều mặt (chính sách `reject`)"],
      ["`ERROR`", "Lỗi xử lý hoặc message không hợp lệ"],
      ["`ORDER_SAVED`, `CUSTOMER_UPDATED`", "Phản hồi của luồng đơn hàng và cập nhật khách"],
    ]),
    H2("Thiết kế CSDL"),
    P("*MySQL* gồm `branches`, `customers`, `customer_photos`, `orders`, `order_items` và `recognition_logs`. Chỉ mục " +
      "`orders(customer_id, ordered_at DESC)` phục vụ việc lấy “đơn gần nhất”. `orders.request_id` là UNIQUE để tạo đơn " +
      "cũng idempotent, còn `recognition_logs.request_id` là UNIQUE vừa phục vụ idempotency vừa là dữ liệu đo đạc. " +
      "`recognition_logs.customer_id` cố ý không có khoá ngoại để dòng nhật ký còn lại khi khách bị xoá do bù trừ. Ảnh " +
      "lưu thành file (đường dẫn tương đối trong `customer_photos.file_path`), không lưu BLOB để CSDL không phình."),
    P("*PostgreSQL* có bảng `face_embeddings(embedding_id, customer_id, embedding vector(512), created_at)` với chỉ mục " +
      "HNSW (`vector_cosine_ops`). `customer_id` là khoá logic sang MySQL; không thể có khoá ngoại thật giữa hai CSDL. " +
      "Nhận diện chạy truy vấn láng giềng gần nhất:"),
    CODE("SELECT customer_id,\n       1 - (embedding <=> q) AS similarity\nFROM face_embeddings\nORDER BY embedding <=> q LIMIT 1;"),
    P("Mỗi khách giữ tối đa năm embedding. Khi nhận ra với độ tin cậy cao nhưng ảnh đủ khác các ảnh đã lưu " +
      "(độ tương tự từ 0,55 đến 0,92), worker thêm embedding mới để việc so khớp bền hơn với ánh sáng và góc mặt."),
    H2("Các luồng xử lý"),
    P("*Luồng A (khách quen).* Client resize, nén ảnh và publish vào `face.requests` kèm `reply_to`, `correlation_id`. " +
      "Một worker rảnh nhận message (prefetch = 1), tra `recognition_logs` theo `request_id`, phát hiện mặt bằng SCRFD, " +
      "căn chỉnh, trích embedding ArcFace chuẩn hoá L2 và hỏi pgvector khách gần nhất theo cosine. Nếu độ tương tự đạt " +
      "ngưỡng *τ*, worker lấy hồ sơ và đơn gần nhất từ MySQL, ghi nhật ký, publish reply rồi mới ack message gốc."),
    P("*Luồng B (khách mới).* Nếu độ tương tự dưới *τ*, worker vào vùng găng (Mục V-F), kiểm tra lại, tạo khách ở " +
      "MySQL, lưu ảnh, ghi embedding vào pgvector rồi trả `NEW_CUSTOMER`. *Luồng C (đơn hàng).* Client gửi " +
      "`order.create` vào `orders`; worker ghi `orders` và `order_items` trong một transaction MySQL, reply rồi ack. {fig:flow} cho thấy chuỗi message của luồng A và B."),
    FIG("flow", "seq_flow.png", "Luồng nhận diện. Bước 1–5 chung; khối alt gồm nhánh khách quen (A) và nhánh khách mới (B, trong khoá advisory, bù trừ khi lỗi); file ảnh ở bước 8 nằm trong kho ảnh dùng chung, còn đường dẫn lưu ở MySQL. Sau đó worker ghi log, đẩy reply vào queue trả lời của client trên broker và chỉ khi đó mới ack (bước 10–13)."),

    // ============================================================ V
    H1("Các cơ chế của hệ phân tán"),
    P("Mỗi mục nêu vấn đề, cơ chế xử lý, liên hệ lý thuyết và cách kiểm chứng."),
    H2("Khả năng mở rộng"),
    P("Nhiều worker cùng đọc `face.requests` theo kiểu competing consumers. Với `prefetch = 1`, mỗi worker chỉ giữ một " +
      "message chưa ack nên việc được chia theo tốc độ xử lý: worker nhanh nhận nhiều hơn, worker bận không bị dồn thêm " +
      "(cân bằng tải động, không cần bộ cân bằng tải riêng). Worker không giữ trạng thái, mọi trạng thái nằm ở CSDL, nên " +
      "thêm hoặc bớt worker bằng `docker compose up -d --scale worker=N` mà không sửa mã. Mỗi request là một đơn vị " +
      "tính toán thô (hàng trăm mili-giây), tương tác ít nên song song hoá hiệu quả. Các thành phần " +
      "tập trung còn lại là broker và hai CSDL (Mục VIII)."),
    P("*Cạm bẫy khi chạy trên một máy.* Mặc định ONNX Runtime khởi động số luồng tính toán bằng số nhân CPU trong *mỗi* " +
      "worker, nên *N* worker tranh nhau toàn bộ nhân (thread oversubscription) và thông lượng không tăng. Chúng tôi " +
      "thêm biến `ORT_THREADS` (0 = tự động) giới hạn số luồng intra-op của mỗi worker, và chọn sao cho số worker × " +
      "`ORT_THREADS` ≤ số luồng CPU. InsightFace không chuyển tiếp tuỳ chọn session nên việc tạo session được bọc lại. " +
      "Ảnh hưởng của tham số này được đo ở Mục VII-C."),
    H2("San tải"),
    P("Hàng đợi bền vững là bộ đệm. Khi ba chi nhánh gửi dồn dập, request xếp hàng chứ không bị từ chối: client chỉ chờ " +
      "lâu hơn (p95 latency tăng) chứ không lỗi, và queue tự về 0 khi tải giảm. Đây là mẫu queue-based load leveling; {fig:queue} là một phép đo (Mục VII-C)."),
    H2("Chịu lỗi"),
    P("Message yêu cầu là persistent (`delivery_mode = 2`) còn reply là transient, queue là quorum queue, ack là thủ công và gửi *sau* khi đã reply. Nếu worker chết trước khi " +
      "ack, broker phát hiện kết nối đóng, trả message về hàng đợi và giao cho worker khác ({fig:crash}); request không " +
      "bao giờ bị mất (at-least-once). Worker cũng tắt êm khi nhận SIGTERM: ngừng nhận message mới và xử lý nốt message " +
      "đang làm."),
    FIG("crash", "seq_crash.png", "Worker chết: Worker 1 chết sau khi ghi log(R) nhưng trước khi reply và ack; RabbitMQ thấy kết nối đóng nên đưa R trở lại queue; Worker 2 thấy log(R) và gửi lại kết quả đã lưu qua queue trả lời (duplicate = true), nên không tạo thêm khách."),
    P("Minh hoạ: nếu mỗi worker hoạt động với xác suất *p* và các lỗi độc " +
      "lập, xác suất có ít nhất một trong *N* worker hoạt động là"),
    EQ("avail", "*A*(*N*) = 1 − (1 − *p*)^{*N*}"),
    P("với *p* = 0,95 cho 0,95; 0,9975 và 0,999875 ứng với *N* = 1, 2, 3. Đây chỉ là minh hoạ lý thuyết (giả định độc " +
      "lập, bỏ qua broker và CSDL); độ tin cậy thực bị giới hạn bởi thành phần tập trung yếu nhất."),
    H2("Gửi lại và idempotency"),
    P("*Vấn đề.* RabbitMQ giao mỗi yêu cầu *ít nhất một lần*, nên cùng một yêu cầu có thể tới worker nhiều lần: (a) chi " +
      "nhánh chờ 10 s không có trả lời nên gửi lại; (b) worker làm xong việc nhưng chết trước khi ack, nên broker giao yêu " +
      "cầu cho worker khác; (c) reply bị mất trên đường về và chi nhánh gửi lại. Nếu bản nào cũng được xử lý trọn vẹn thì " +
      "một người có thể bị đăng ký hai lần, hoặc một đơn bị lưu hai lần."),
    P("*Cách giải quyết.* Mỗi yêu cầu mang một `request_id` (một UUID) do chi nhánh tạo một lần và dùng lại cho mọi lần " +
      "gửi lại. Worker dùng mã này qua ba bước:"),
    OL("Trước khi làm bất cứ việc gì, worker tra `request_id` trong bảng `recognition_logs`.",
       "Nếu đã có dòng, yêu cầu này đã được xử lý: worker trả lại kết quả đã lưu kèm `duplicate = true`, không chạy lại " +
       "model và không tạo bản ghi nào.",
       "Nếu chưa có, worker xử lý rồi ghi dòng nhật ký. Cột `request_id` là UNIQUE, nên nếu hai bản chạy cùng lúc thì " +
       "chỉ một dòng được ghi, bản còn lại được báo là trùng."),
    P("Đơn hàng dùng cùng ý tưởng nhờ cột UNIQUE `orders.request_id`: gửi lặp một đơn sẽ nhận lại đơn đã lưu. Cập nhật " +
      "tên hoặc số điện thoại của khách ghi giá trị tuyệt đối nên lặp lại cũng không đổi gì. *Ví dụ:* chi nhánh D gửi yêu " +
      "cầu R; worker 1 lưu kết quả rồi bị giết trước khi trả lời; RabbitMQ giao R cho worker 2, worker này thấy R đã có " +
      "trong nhật ký nên trả lại kết quả đã lưu. Chi nhánh nhận đúng một câu trả lời và CSDL có đúng một dòng nhật ký " +
      "(đo thật ở {tab:fault})."),
    P("*Liên hệ lý thuyết.* Giao ít nhất một lần cộng với phát hiện bản trùng cho kết quả *xử lý đúng một lần trên thực " +
      "tế* (effectively-once). Ý tưởng này là kỹ thuật at-most-once trong {cite:liskov}, nơi máy chủ nhớ những định danh " +
      "message đã xử lý; ở đây định danh là UUID ngẫu nhiên thay vì timestamp, nên các máy chi nhánh không cần đồng bộ " +
      "đồng hồ. Nếu reply không giao được vì chi nhánh đã ngắt kết nối, worker vẫn ack yêu cầu; khi chi nhánh gửi lại R, " +
      "nó nhận kết quả từ nhật ký."),
    H2("Nhất quán giữa hai CSDL"),
    P("MySQL và PostgreSQL không có transaction chung. Two-phase commit chậm, dễ treo và khó áp dụng cho hai hệ không " +
      "đồng nhất, nên chúng tôi dùng saga đơn giản {cite:sagas}: các bước ghi cục bộ theo thứ tự, kèm bù trừ."),
    OL("MySQL: thêm khách, ghi file ảnh, thêm dòng ảnh.",
       "pgvector: thêm embedding (commit khi thoát vùng găng).",
       "Nếu bước 2 lỗi (kể cả lúc commit): xoá khách vừa tạo và file ảnh (`_compensate`)."),
    P("Thứ tự “MySQL trước, pgvector sau” là cố ý: embedding, thứ khiến khách *có thể được nhận ra*, chỉ xuất hiện khi " +
      "khách và ảnh đã tồn tại đầy đủ. Hai kiểu dữ liệu mồ côi vẫn có thể xảy ra nếu chính bước bù trừ lỗi hoặc worker " +
      "chết giữa hai bước: khách không có embedding (không bao giờ nhận ra) và embedding không có khách. Chúng được xử " +
      "lý bằng hai lớp: worker tự xoá embedding mồ côi khi gặp trong lúc nhận diện, và `tools.reconcile` quét, dọn định " +
      "kỳ (`--fix`). Test `SagaTests` cho pgvector lỗi khi ghi embedding: sau đó không còn khách và ảnh, và khi pgvector " +
      "phục hồi thì message được giao lại thành công."),
    H2("Đồng thời: hai chi nhánh cùng gặp một người mới"),
    P("Chuỗi “tìm → (chưa có) → thêm” không nguyên tử. Hai worker xử lý hai ảnh của cùng một người mới có thể cùng không " +
      "thấy và cùng tạo khách, dẫn đến hai khách cho một người. Đây là race condition giữa các tiến trình phân tán; " +
      "mutex cục bộ trong một process (như `threading.Lock` của Python) không giúp được. {tab:lock} so sánh hai phương án."),
    TAB("lock", "Các phương án chống đăng ký trùng", [0.26, 0.34, 0.40], ["l", "l", "l"],
      ["Phương án", "Ưu", "Nhược"], [
        ["**A. Khoá** (đã cài)", "Không bao giờ tạo trùng", "Nối tiếp hoá việc ghi khách mới; PostgreSQL là điểm lỗi đơn của khoá"],
        ["B. Chấp nhận, gộp sau", "Không nghẽn", "Có lúc dữ liệu trùng; cần logic gộp đơn và ảnh"],
      ]),
    P("Phương án A bao “tìm lại → tạo khách → ghi embedding” bằng `pg_advisory_xact_lock`. Đây là loại trừ tương hỗ tập " +
      "trung {cite:tanenbaum} với PostgreSQL làm coordinator: đơn giản và an toàn, với nhược điểm kinh điển là điểm " +
      "lỗi đơn và nút cổ chai. Ở đây chấp nhận được vì (a) vùng găng không chứa suy luận của model, chỉ một lần tìm vector và vài thao tác ghi nhỏ, (b) chỉ áp dụng cho đường " +
      "khách mới, còn đường khách quen (phần lớn tải) không cần khoá, và (c) advisory transaction lock tự nhả khi " +
      "transaction kết thúc hoặc kết nối đứt, nên worker chết không giữ khoá mãi và không cần lease riêng."),
    P("Sau khi lấy khoá, worker *kiểm tra lại* (double-checked locking): trong lúc chờ, worker khác có thể vừa đăng ký " +
      "chính người này, khi đó trả `RECOGNIZED`. Worker cũng kiểm tra lại `recognition_logs` để xử lý hai bản của cùng " +
      "một `request_id`. `ConcurrencyTests` chạy tám “worker” (thread) trên kho giả dùng chung với khoá mô phỏng, xử lý " +
      "các ảnh khác nhau của một người mới: đúng một khách, một `NEW_CUSTOMER`, bảy `RECOGNIZED`, và không lúc nào có " +
      "quá một worker trong vùng găng. Test đối chứng bỏ khoá cho nhiều hơn một khách, chứng tỏ khoá thực sự ngăn được race. " +
      "Kịch bản này sau đó được lặp lại trên hệ thống thật ({tab:fault}): sáu request mang cùng một ảnh của một tình nguyện " +
      "viên chưa enroll được gửi với `auto_enroll = true` tới ba worker; ngay sau đó độ sâu hàng đợi đọc được là 3, nghĩa là " +
      "ba request còn lại đã được các worker lấy. Kết quả là một `NEW_CUSTOMER` và năm `RECOGNIZED`, đúng một dòng khách " +
      "mới, và `tools.reconcile` sạch. Lần chạy này không có đối chứng bỏ khoá, nên chỉ cho thấy không tạo khách trùng, " +
      "không cho biết worker nào đã chờ khoá bao lâu."),
    H2("Bảo mật"),
    P("{tab:sec} tóm tắt các mối đe doạ chính và biện pháp tương ứng."),
    TAB("sec", "Mối đe doạ và biện pháp", [0.36, 0.64], ["l", "l"], ["Mối đe doạ", "Biện pháp"], [
      ["Nghe lén, sửa đổi đường truyền", "Lớp `amqps` tuỳ chọn (cổng 5671; client xác thực server bằng `ca.pem`); chưa được thử trong thực nghiệm, vốn chạy trong mạng ảo ZeroTier đã mã hoá"],
      ["Chi nhánh giả mạo hoặc lạm quyền", "Mỗi chi nhánh một tài khoản với quyền tối thiểu: chỉ publish vào `coffee.requests`, chỉ cấu hình và đọc queue có tên `reply.*` (exclusive với kết nối tạo ra nó); không đọc được `face.requests`; trường `branch_id` trong message vẫn do máy gửi tự khai (mục VIII)"],
      ["Dùng trái phép máy quầy chi nhánh", "Trang web chi nhánh hỏi tài khoản RabbitMQ của đúng chi nhánh đó; mật khẩu do chính broker kiểm, nên máy chi nhánh không lưu mật khẩu nào, và mỗi máy được ghim vào một chi nhánh nên tài khoản của chi nhánh khác bị từ chối. Đã kiểm trên broker của máy lab ngày 14/11 với chi nhánh D: mật khẩu sai và tài khoản `branch_a` đều bị từ chối"],
      ["Công cụ giám sát vô tình sửa dữ liệu", "Trang quản trị chỉ đọc (`admin/`) chỉ nghe ở 127.0.0.1 và được xem qua đường hầm SSH; có đăng nhập riêng với khoá tạm khi nhập sai nhiều lần, chỉ gửi yêu cầu GET tới management API của RabbitMQ và mở phiên CSDL ở chế độ chỉ đọc. Trên MySQL 8.0.46 của máy lab, lệnh ghi từ phiên này bị từ chối với lỗi 1792; PostgreSQL cũng từ chối"],
      ["Message độc hại", "Kiểm tra chặt mọi trường; message hỏng vào DLQ, không làm sập worker"],
      ["Lộ dữ liệu sinh trắc học", "File ảnh quyền 0750/0640, chống path traversal, worker chạy bằng user không phải root"],
      ["Quyền riêng tư", "Chỉ thu ảnh khi người chụp đồng ý; xoá ảnh sau khi kết thúc môn học"],
      ["Mạo danh bằng ảnh hoặc video", "Chưa xử lý (chưa có liveness detection)"],
    ]),
    H2("Tính không đồng nhất và tính trong suốt"),
    P("Giao thức AMQP với JSON độc lập ngôn ngữ và hệ điều hành: client viết bằng Python, Java hay Go đều nối được miễn là " +
      "tuân theo hợp đồng message. Về tính trong suốt {cite:coulouris}, chi nhánh gọi thao tác “nhận diện” mà không biết vị " +
      "trí hay số lượng worker (location và replication transparency); worker chết được che giấu khỏi người dùng " +
      "(failure transparency); thêm worker không đổi ứng dụng (scaling transparency); các chi nhánh dùng chung dữ liệu " +
      "mà không can thiệp nhau nhờ khoá và idempotency (concurrency transparency)."),

    // ============================================================ VI
    H1("Cài đặt"),
    H2("Cấu trúc mã nguồn"),
    P("Mã Python chia thành `common/` (hợp đồng message và bộ kiểm tra trong `messages.py`, khai báo topology trong " +
      "`rabbit.py`, cấu hình qua biến môi trường trong `config.py` để cùng một mã chạy trên nhiều máy), `worker/` " +
      "(`face_engine.py` cho SCRFD + ArcFace và bộ lọc chất lượng; `service.py` cho nghiệp vụ và các cơ chế phân tán: " +
      "idempotency, khoá, saga; `handler.py` cho quy tắc ack/retry/DLQ; `worker.py` cho vòng lặp consumer, kết nối lại " +
      "và tắt êm; các repo MySQL, pgvector và kho ảnh), `client/` (`rpc_client.py`, giao diện Tkinter có webcam, giao diện web chạy tại máy chi nhánh qua `web.py` có đăng nhập theo chi nhánh, và CLI), " +
      "`admin/` (trang giám sát chỉ đọc), `tools/` (enroll, benchmark, quét ngưỡng, phân tích theo cặp, reconcile, chaos, so embedding " +
      "trong CSDL với bộ dữ liệu có nhãn, và công cụ gộp khách chỉ xem trước trừ khi có `--apply`), `scripts/b4/` (chạy bốn chi nhánh " +
      "cùng lúc) và `tests/` (129 unit test trên repo giả)."),
    H2("Pipeline nhận diện"),
    OL("Giải mã JPEG thành ảnh BGR.",
       "SCRFD phát hiện mặt và năm điểm mốc; InsightFace căn chỉnh mặt về 112 × 112.",
       "ArcFace (`w600k_r50`) cho embedding 512 chiều, chuẩn hoá L2. Với vector đơn vị, độ tương tự cosine bằng tích vô " +
       "hướng, và `1 - (embedding <=> q)` trong pgvector chính là độ tương tự cosine.",
       "Lọc chất lượng (`evaluate_faces`): bỏ mặt có điểm phát hiện < 0,5 hoặc cạnh ngắn < `MIN_FACE_PX` (60 px); ảnh có độ nét vùng mặt (phương sai Laplacian, chuẩn hoá về " +
       "112 × 112) dưới 30 cho `NO_FACE` kèm lý do. Nhờ vậy mặt bị che hoặc mờ không tạo khách rác. Khi có nhiều mặt, " +
       "chính sách `largest` lấy mặt có khung bao lớn nhất (thường là mặt gần camera nhất), `reject` yêu cầu chụp lại."),
    P("`SIM_THRESHOLD` được chỉnh bằng thực nghiệm (Mục VII-B). `MIN_FACE_PX` và `MIN_BLUR_VAR` phụ thuộc camera và ánh sáng; " +
      "cả 77 ảnh đánh giá đều qua các giá trị mặc định nên chúng không được chỉnh."),
    H2("Worker và quy tắc ack"),
    P("Mỗi worker là một process độc lập với `basic_qos(prefetch_count=1)`, `confirm_delivery()` và ack thủ công. " +
      "{tab:ack} là toàn bộ chính sách xử lý một message (`worker/handler.py`). Số lần giao lấy từ header " +
      "`x-delivery-count` của quorum queue."),
    TAB("ack", "Chính sách ack", [0.4, 0.38, 0.22], ["l", "l", "l"],
      ["Kết quả xử lý", "Message gốc", "Client nhận"], [
        ["Thành công", "**reply trước, ack sau**", "kết quả"],
        ["Message hỏng", "`reject(requeue=false)` → DLQ", "`ERROR`"],
        ["Lỗi nghiệp vụ (vd. khách không tồn tại)", "ack (thử lại vô ích)", "`ERROR`"],
        ["Lỗi tạm thời, còn lượt thử", "`nack(requeue=true)`", "(chưa)"],
        ["Lỗi tạm thời, hết `MAX_DELIVERIES` = 3", "`reject` → DLQ", "`ERROR`"],
        ["Worker chết trước khi ack", "broker giao lại", "kết quả"],
      ]),
    H2("Client và triển khai"),
    P("`BranchRpcClient` tạo queue trả lời riêng và publish với `correlation_id = request_id` kèm `reply_to`, bật " +
      "publisher confirms và cờ `mandatory` để biết broker đã nhận message. Hết timeout 10 s thì gửi lại với *cùng* " +
      "`request_id`, tối đa ba lần thử (tính cả lần đầu); mất kết nối thì nối lại với back-off. Lỗi quyền (403) hoặc thiếu exchange (404) được " +
      "báo rõ nguyên nhân thay vì chờ timeout. Giao diện Tkinter chạy mọi lời gọi mạng ở thread nền. Trên server, " +
      "`scripts/quickstart.sh 3` dựng broker, hai CSDL, ba worker và tài khoản chi nhánh. Máy chi nhánh thường chạy giao diện " +
      "web, `python -m client.web --branch D --host <server>`: trang hỏi tài khoản của đúng chi nhánh đó trước khi gửi bất kỳ " +
      "yêu cầu nào. Giao diện Tkinter (`client.gui`) và CLI vẫn dùng được."),

    // ============================================================ VII
    H1("Đánh giá thực nghiệm"),
    H2("Thiết lập"),
    P("Server là Intel Core i5-11500B 3,30 GHz (6 nhân, 12 luồng), RAM 7,4 GiB, Ubuntu 22.04.5 LTS. RabbitMQ, " +
      "PostgreSQL, MySQL và mọi worker chạy bằng Docker Compose trên *cùng một máy* này, nên các phép đo co giãn bị giới " +
      "hạn bởi CPU của một máy. Một client chi nhánh chạy trên laptop riêng (Windows, Ubuntu 24.04 trong WSL2) và nối " +
      "tới server qua mạng ảo ZeroTier riêng tạo cho dự án. Mọi số đo dưới đây lấy trước khi worker bắt đầu gắn thêm " +
      "`frequent_items` (một truy vấn tổng hợp chỉ đọc cho mỗi khách quen), nên với mã hiện tại thời gian xử lý khách quen " +
      "có thể cao hơn một chút; ảnh hưởng đó chưa được đo lại. Mọi phép đo thời gian giữa nhiều máy chỉ dùng " +
      "`recognition_logs.created_at` do một đồng hồ MySQL ghi, không dùng `captured_at` do máy chi nhánh ghi: trong một buổi, " +
      "mốc giờ của máy chi nhánh lệch tới 6,85 s, lớn hơn nhiều so với các khoảng trễ cỡ 0,4 s cần đo."),
    P("Để kiểm tra đa máy, client đó kết nối tới RabbitMQ của server bằng tài khoản quyền tối thiểu `branch_b` và gửi " +
      "yêu cầu nhận diện một khách có đơn gần nhất đặt tại chi nhánh A. Phản hồi là *khách quen* kèm đơn của chi nhánh A; " +
      "giao diện quản trị RabbitMQ hiện kết nối `branch-B-laptop` từ địa chỉ ZeroTier của laptop, thời gian xử lý ở worker là " +
      "319–470 ms và dòng log ghi `branch_id = B`. Phép kiểm tra này xác nhận đường đi phân tán và trạng thái dùng chung giữa " +
      "các chi nhánh; đây là kiểm tra chức năng, không phải ước lượng độ chính xác. Trong một buổi tập dượt sau đó, khách mới " +
      "tạo và đơn lưu trên giao diện chi nhánh A được nhận ra, kèm đúng đơn đó, trên giao diện chi nhánh B (độ giống 1,000 chỉ " +
      "vì dùng lại chính ảnh enroll). Một lần kiểm tra trực tiếp bằng client Tkinter và webcam của server (tắt tự đăng ký) đã " +
      "nhận ra khuôn mặt của một tác giả là khách đã enroll với độ giống 0,724 trong 302 ms, từ một khung hình webcam trực " +
      "tiếp chứ không phải ảnh đã lưu; đây chỉ là một quan sát, không phải ước lượng độ chính xác."),
    P("Dữ liệu: 11 bạn trong lớp (có đồng ý), tổng cộng 77 ảnh (6–14 ảnh mỗi người). Bảy " +
      "người được enroll bằng 2 ảnh mỗi người, chọn bằng mắt trước khi chạy bất kỳ phép đo nào; " +
      "39 ảnh còn lại của họ là ảnh test của khách quen. Bốn người còn lại (24 ảnh) không bao giờ được enroll và đóng vai " +
      "người lạ để đo nhận nhầm. Cả 77 ảnh đều qua bước kiểm tra chất lượng nên không ảnh nào bị loại, và ảnh test không " +
      "được lọc."),
    H2("Chọn ngưỡng"),
    P("`tools.eval_threshold` quét ngưỡng cosine *τ* từ 0,30 đến 0,60 với bước 0,02. *Nhận đúng* là khách quen được nhận " +
      "đúng người; *FRR* là bỏ sót (hệ thống tạo khách trùng); *nhầm người* là khách bị hiển thị đơn của người khác, lỗi " +
      "nghiêm trọng nhất; *FAR* là người lạ bị nhận thành khách đã có. {tab:thr} cho kết quả trên 39 ảnh khách quen và 24 " +
      "ảnh người lạ (mỗi ảnh khách quen tương ứng 2,6 điểm phần trăm, mỗi ảnh người lạ 4,2); các ngưỡng liên tiếp có kết quả giống nhau được gộp " +
      "thành một dòng. Với mọi *τ* từ 0,30 đến 0,46, cả 39 khách quen được nhận đúng người và cả 24 người lạ bị từ chối, " +
      "tức cả 63 quyết định (39 + 24) đều đúng. Từ *τ* = 0,48 có một ảnh khách quen, độ giống nằm giữa 0,46 và 0,48, bị bỏ sót (FRR " +
      "2,6%), và FRR tăng đến 7,7% ở *τ* = 0,60. Ở mọi ngưỡng trong dải quét không có khách quen nào bị nhận thành người " +
      "khác và không người lạ nào được chấp nhận, nên độ giống cao nhất của mỗi người lạ với gallery luôn dưới 0,30."),
    P("Vì dải chính xác cao nhất trải từ 0,30 đến 0,46, áp dụng quy tắc hoà của công cụ là chọn ngưỡng cao hơn (hiển thị " +
      "đơn của người khác tệ hơn tạo khách trùng) và chọn *τ* = 0,46. Giá trị này được triển khai qua `SIM_THRESHOLD`; " +
      "mặc định trong mã là 0,45 và cho kết quả y hệt trên dữ liệu này. Kết quả chỉ mang tính chỉ báo: gallery chỉ có bảy " +
      "người, 0/24 người lạ được chấp nhận chỉ chặn FAR ở mức không quá khoảng 12,5% (độ tin cậy 95%, quy tắc ba) và FAR sẽ tăng khi " +
      "gallery lớn hơn, còn ảnh khách quen yếu nhất nằm sát trên ngưỡng đã chọn nên điểm tối ưu có thể dịch chuyển với " +
      "camera hoặc ánh sáng khác."),
    P("*Kiểm tra theo cặp.* So sánh mọi ảnh với mọi ảnh khác (77 ảnh: 262 cặp cùng người và 2664 cặp khác người) không " +
      "phụ thuộc cách chia gallery và probe. Độ giống của cặp cùng người có giá trị nhỏ nhất 0,257, phân vị 5% là 0,440 và " +
      "trung vị 0,642; của cặp khác người có trung vị 0,095, phân vị 99% là 0,275 và lớn nhất 0,338, nên không cặp khác " +
      "người nào đạt 0,34. Các cặp dùng chung ảnh nên không độc lập, vì vậy các số này mô tả dữ liệu chứ không phải khoảng tin cậy. " +
      "Tuy nhiên 20 trong 262 cặp cùng người (7,6%) thấp hơn *τ* = 0,46 (3,8% thấp hơn 0,42). Vì vậy " +
      "với một ảnh enroll bất kỳ, khách bị bỏ sót nhiều hơn mức 0,0% của {tab:thr} ở *τ* = 0,46 (2,6% ở 0,48), vốn dùng hai ảnh được chọn trước cho mỗi " +
      "người: trong một lần chạy thử trực tiếp, khách được enroll bằng một ảnh đã không được nhận ra từ một ảnh khác của " +
      "chính họ (độ giống 0,335). Việc so khớp lấy embedding gần nhất trong tối đa năm embedding của mỗi khách, nên enroll " +
      "nhiều hơn một ảnh đa dạng sẽ làm giảm tỉ lệ bỏ sót, và bỏ sót chỉ tạo một hồ sơ trùng chứ không nhận sai người. Vẫn " +
      "giữ *τ* = 0,46: cũng trong lần chạy thử đó, một người lạ đạt 0,383 so với một khách đã enroll trong CSDL thật, cao " +
      "hơn mọi cặp khác người trong bộ dữ liệu, nên biên an toàn càng quan trọng khi lượng khách tăng."),
    P("*Một ca trùng gặp trong CSDL thật.* Một tình nguyện viên bị tạo hai hồ sơ. Lúc tạo hồ sơ thứ hai, độ giống cao nhất " +
      "với hồ sơ đã có của chính người đó là 0,447, ngay dưới *τ* = 0,46, nên worker tạo khách mới đúng như thiết kế. Một " +
      "embedding thêm sau đó nâng độ giống cao nhất giữa hai hồ sơ lên 0,498 và `tools.reconcile` báo cặp này. So từng " +
      "embedding trong CSDL với bộ dữ liệu có nhãn (`tools/db_vs_dataset.py`) cho thấy hai hồ sơ là cùng một người; sau khi " +
      "sao lưu, `tools.merge_customers` gộp hai hồ sơ ngày 8/11 (ảnh, đơn và embedding dồn về một hồ sơ) và `tools.reconcile` " +
      "sạch trở lại. Ca duy nhất này minh hoạ con số 7,6% ở trên: bỏ sót chỉ tạo hồ sơ trùng, có thể phát hiện và sửa bằng " +
      "đối soát, chứ không nhận sai người."),
    TAB("thr", "Quét ngưỡng: 39 ảnh khách quen và 24 ảnh người lạ (gộp các dòng giống nhau)", [0.2, 0.22, 0.12, 0.25, 0.21], ["c", "c", "c", "c", "c"],
      ["*τ*", "Nhận đúng", "FRR", "Nhầm người", "FAR"], [
        ["0,30–0,46", "100,0%", "0,0%", "0,0%", "0,0%"],
        ["0,48–0,54", "97,4%", "2,6%", "0,0%", "0,0%"],
        ["0,56–0,58", "94,9%", "5,1%", "0,0%", "0,0%"],
        ["0,60", "92,3%", "7,7%", "0,0%", "0,0%"],
      ]),
    H2("Khả năng mở rộng"),
    P("Mọi lần đo chạy trên cùng một máy, cùng một ảnh của khách đã đăng ký, `auto_enroll=false` (không tạo khách mới) và " +
      "`DEMO_DELAY_MS=0`. Có hai phép đo độc lập; mỗi cấu hình chỉ đo một lần nên không có số liệu về phương sai."),
    P("*Benchmark.* `tools.benchmark` gửi 100 request từ 12 client song song với `ORT_THREADS=2` ({tab:bench}, {fig:bench})."),
    TAB("bench", "Benchmark: 100 request, 12 client", [0.14, 0.17, 0.15, 0.15, 0.39], ["c", "c", "c", "c", "c"],
      ["Worker", "req/s", "p50 (ms)", "p95 (ms)", "Chia việc giữa worker"], [
        ["1", "3,45", "3456", "3519", "100"],
        ["2", "6,34", "1854", "1937", "50 / 50"],
        ["4", "8,26", "1416", "1571", "26 / 24 / 25 / 25"],
      ]),
    FIG("bench", "bench.png", "Kết quả benchmark: thông lượng (trái) và latency đầu-cuối (phải) theo số worker."),
    P("Mọi lần đo đều hoàn thành 100/100 request, không lỗi và không phải gửi lại; việc được chia gần như đều (competing " +
      "consumers với `prefetch = 1`). Thông lượng tăng 1,84× với hai worker (hiệu suất 92%) và 2,39× với bốn (60%): có " +
      "tăng nhưng không tuyến tính. Latency p50 giảm từ 3,5 s xuống 1,4 s. Con số lớn vì 12 client gửi liên tục nên " +
      "latency chủ yếu là thời gian chờ trong hàng đợi, đúng như định luật Little {cite:little}: *W* = *L*/*λ* với *L* = 12 request trong hệ thống và *λ* là thông lượng: " +
      "12/3,45 = 3,48 s; 12/6,34 = 1,89 s; 12/8,26 = 1,45 s. Thêm worker rút ngắn thời gian chờ, không rút ngắn thời gian xử lý của một request."),
    FIG("lat", "latency_req.png", "Latency của từng request trong benchmark theo thứ tự gửi (12 client; cùng các lần đo với {tab:bench})."),
    P("{fig:lat} cho thấy từng request. 12 request đầu, do 12 client gửi gần như cùng lúc, xếp hàng sau nhau nên latency tăng khoảng " +
      "một thời gian xử lý cho mỗi request đứng trước: đợt dồn được hấp thụ chứ không bị từ chối. Sau đó latency đi ngang ở khoảng " +
      "3,5 s, 1,9 s và 1,4 s với một, hai và bốn worker, đúng là thời gian chờ theo định luật Little."),
    P("*Đo dồn tải.* `scripts/measure_drain.sh` dồn 200 request bằng `tools.chaos flood`. Mỗi request xử lý xong để lại " +
      "một dòng trong MySQL `recognition_logs`, nên “xong” nghĩa là đủ 200 dòng mới và thời gian lấy từ `created_at`, " +
      "không phụ thuộc số liệu hàng đợi của RabbitMQ vốn cập nhật trễ; *req/s* là 200 chia cho khoảng thời gian giữa dòng đầu và dòng cuối trong số đó. Chúng tôi đo hai lần: với số luồng mặc định " +
      "của ONNX Runtime và với `ORT_THREADS=2` ({tab:drain}, {fig:scale}). *ms/req* là thời gian xử lý trung bình mỗi request đo ngay trong " +
      "worker (`recognition_logs.latency_ms`); tăng tốc tính so với một worker có cùng cấu hình luồng, còn hiệu suất bằng tăng tốc chia cho " +
      "số worker (cả hai tính từ thông lượng chưa làm tròn)."),
    TAB("drain", "Đo dồn tải: 200 request", [0.13, 0.27, 0.12, 0.17, 0.15, 0.16], ["c", "l", "c", "c", "c", "c"],
      ["Worker", "Luồng ONNX", "req/s", "ms/req", "Tăng tốc", "Hiệu suất"], [
        ["1", "mặc định", "6,72", "147", "1,00×", "100%"],
        ["2", "mặc định", "6,50", "305", "0,97×", "48%"],
        ["3", "mặc định", "5,49", "549", "0,82×", "27%"],
        ["1", "2 mỗi worker", "3,48", "285", "1,00×", "100%"],
        ["2", "2 mỗi worker", "6,16", "324", "1,77×", "88%"],
        ["4", "2 mỗi worker", "8,36", "479", "2,40×", "60%"],
        ["6", "2 mỗi worker", "9,06", "669", "2,60×", "43%"],
      ]),
    FIG("scale", "scaling.png", "Thông lượng theo số worker: luồng ONNX mặc định so với hai luồng mỗi worker. Dấu là điểm đã đo; đường nét đứt là 3,48 req/s × *N*, tức một worker đã giới hạn luồng được nhân tuyến tính lý tưởng."),
    OL("*Cấu hình mặc định không mở rộng được trên một máy.* Thông lượng đi từ 6,72 sang 6,50 rồi 5,49 req/s khi tăng từ 1 lên 2 rồi 3 " +
       "worker, còn thời gian xử lý mỗi request tăng từ 147 lên 549 ms. ONNX Runtime đặt kích thước nhóm luồng theo số nhân của máy cho *mỗi* worker, " +
       "nên *N* worker tranh nhau cùng các CPU (thread oversubscription) trong khi tổng tài nguyên không đổi.",
       "*Giới hạn luồng cho mỗi worker* (`ORT_THREADS=2`, số worker × 2 ≤ 12) làm worker thêm vào có tác dụng: 3,48; " +
       "6,16; 8,36 và 9,06 req/s với 1, 2, 4 và 6 worker (tăng 1,77×, 2,40×, 2,60×).",
       "*Giới hạn của phép đo.* Máy chỉ có sáu nhân vật lý, lại phải chạy MySQL, PostgreSQL, RabbitMQ và công cụ tạo " +
       "tải; một worker mặc định (6,72 req/s) đã dùng gần hết CPU. Vì vậy trần của máy này khoảng 9 req/s và cấu hình " +
       "tốt nhất chỉ nhanh hơn cấu hình mặc định khoảng 35%. Đây là giới hạn của tài nguyên dùng chung trên một máy, " +
       "không phải của thiết kế: việc luôn được chia đều (200 cho một worker, 100/100 cho hai, 50/50/49/51 cho bốn và 34/33/34/33/33/33 cho sáu). Muốn tăng " +
       "tiếp phải thêm máy, mỗi máy mang CPU riêng; phép đo nhiều máy chưa thực hiện (Mục VIII)."),
    P("*Độ sâu hàng đợi khi dồn tải.* Để thấy trực tiếp việc san tải, `tools.chaos watch` lấy mẫu số message đang chờ trong " +
      "`face.requests` mỗi 0,5 s trong lúc `tools.chaos flood` gửi 200 request cùng lúc ({fig:queue}; `ORT_THREADS=2`). Queue nhảy từ 0 " +
      "lên khoảng 200 trong một chu kỳ lấy mẫu rồi giảm gần như tuyến tính, tức tốc độ phục vụ ổn định và broker nhận toàn bộ " +
      "đợt dồn: từ 198 về 0 trong 57,6 s với một worker (3,4 request/s) và từ 197 về 0 trong 25,1 s với ba worker (7,9 request/s). " +
      "Các tốc độ này phù hợp với phép đo dồn tải ở {tab:drain} (3,48 req/s với một worker; 6,16 và 8,36 với hai và bốn). Số đếm " +
      "không tính message worker đang cầm chưa ack, và mỗi cấu hình chỉ đo một lần."),
    FIG("queue", "queue_depth.png", "Số message đang chờ trong `face.requests` sau đợt dồn 200 request, một worker so với ba worker (lấy mẫu mỗi 0,5 s; mốc 0 là mẫu cuối cùng trước khi đợt dồn xuất hiện)."),
    H2("Thí nghiệm chịu lỗi"),
    P("{tab:fault} liệt kê từng kịch bản tiêm lỗi, cách thực hiện và kết quả quan sát được."),
    TAB("fault", "Kết quả tiêm lỗi", [0.34, 0.66], ["l", "l"], ["Kịch bản (cách làm)", "Kết quả"], [
      ["Kill worker khi đang xử lý (`DEMO_DELAY_MS=4000`, `demo_kill_worker.sh`)",
       "**Đạt (hai lần chạy).** Lần hai, có đủ log (`results/kill_demo_*.txt`): `worker-8` ghi dòng log của request `593d6f0a…`, vào khoảng dừng 4 s chủ động rồi bị giết; `worker-7` nhận lại đúng request đó sau khoảng 0,4 s và, sau khoảng dừng 4 s của chính nó, trả lời từ kết quả đã lưu (`duplicate = true`, không phân tích khuôn mặt lần hai, 16 ms). `recognition_logs` có đúng một dòng cho request, hai hàng đợi chính rỗng, số message trong các DLQ không đổi và `tools.reconcile` sạch. Lần chạy trước đó (request `96f70b22…`) cho cùng kết quả (giao lại sau khoảng 0,3 s, phát lại 17 ms, không dòng trùng) nhưng không kèm log gốc. Cả hai lần dùng khoảng dừng chủ động và chỉ thử một điểm lỗi: sau khi ghi DB, trước khi reply và ack"],
      ["Client timeout rồi gửi lại (cùng `request_id` hai lần)", "**Đạt.** Reply lần hai có `duplicate = true`; không tạo khách mới"],
      ["Message hỏng (`tools.chaos poison`)", "**Đạt.** Ba message vào `face.requests.dlq` (không phải JSON; thiếu `image_b64`; `image_b64` không hợp lệ); `orders.dlq` vẫn trống"],
      ["Dồn tải (`tools.chaos flood`)", "**Đạt.** Đợt 300 request làm queue lên 296 message. Ở đợt 200 request sau đó ({fig:queue}), queue đi từ 197 về 0 trong 25 s với ba worker; ở một buổi tập dượt trước đó, số message chưa ack luôn bằng ba (3 worker × `prefetch = 1`) và số khách không đổi"],
      ["pgvector sập khi enroll (`SagaTests`)", "**Đạt.** Không còn dữ liệu mồ côi"],
      ["Nhiều worker cùng gặp một người mới (`ConcurrencyTests`)", "**Đạt** 3/3, gồm test đối chứng bỏ khoá"],
      ["Sáu bản ảnh của một người mới tới ba worker (`tools.chaos flood --enroll`, PostgreSQL thật)", "**Đạt.** `recognition_logs` có 1 `NEW_CUSTOMER` và 5 `RECOGNIZED`; bảng khách tăng đúng một dòng (6 lên 7); `tools.reconcile` sạch"],
      ["Đối soát hai CSDL sau mọi lần chạy (`tools.reconcile`)", "**Đạt.** Không khách hay embedding mồ côi, không cặp khách trùng"],
    ], { size: 15 }),
    H2("Kiểm thử tự động"),
    P("Cả 129 unit test đều đạt (`python -m unittest discover -s tests -t .`). Chúng bao gồm: khách mới rồi khách quen ở " +
      "chi nhánh khác kèm đơn; `NO_FACE` và `MULTIPLE_FACES` không tạo khách rác; idempotency của nhận diện và đơn hàng; " +
      "giao lại sau khi worker chết; khoá chống trùng khách có đối chứng; bù trừ saga; quy tắc ack/retry/DLQ của " +
      "`handler`; kiểm tra message; lọc ảnh mờ bằng ảnh tổng hợp; tính percentile và quét ngưỡng trên embedding tổng hợp " +
      "có đáp án biết trước; xếp hạng món khách gọi nhiều nhất; lớp HTTP của giao diện web (kiểm tra đầu vào, ánh xạ lỗi, từ chối yêu cầu từ trang web khác) cùng đăng nhập và phiên theo chi nhánh; trang quản trị chỉ đọc (đăng nhập, khoá tạm, quy tắc yêu cầu và các phép tổng hợp); kế hoạch gộp của công cụ gộp khách; và thứ tự reply rồi mới ack (test thất bại nếu handler ack trước khi trả lời). Các test dùng repo giả trong bộ nhớ nên kiểm chứng *logic* của cơ chế; kiểm chứng trên " +
      "RabbitMQ, MySQL, PostgreSQL thật là phần thực nghiệm ở trên."),

    // ============================================================ VIII
    H1("Hạn chế và hướng phát triển"),
    P("*Hạn chế.*"),
    UL("**Điểm lỗi đơn:** broker và mỗi CSDL chỉ có một bản trong cấu hình này.",
       "**Phép đo co giãn chỉ trên một máy** (6 nhân vật lý) nên bị chặn bởi CPU dùng chung; chưa đo worker chạy trên nhiều máy.",
       "**Thư mục ảnh dùng chung:** worker trên nhiều máy cần NFS hoặc object storage (MinIO/S3); `PhotoStore` là module duy nhất cần thay.",
       "**Khoá dựa vào PostgreSQL** nên sẽ nghẽn nếu tỉ lệ khách mới rất cao.",
       "**Chưa có liveness detection:** ảnh in hoặc màn hình điện thoại có thể đánh lừa hệ thống.",
       "**TLS chưa được thử:** lớp `amqps` và script sinh chứng chỉ đã có nhưng chưa chạy trên mạng thật; phép kiểm tra đa máy dùng AMQP thường trong mạng ảo ZeroTier.",
       "**Bằng chứng đa máy mới từ một chi nhánh ở xa mỗi lần:** các phép kiểm tra ở Mục VII dùng một laptop; lần chạy bốn chi nhánh trên bốn máy gửi cùng lúc đã chuẩn bị sẵn (`scripts/b4/`), nhưng kết quả chưa có trong phiên bản này.",
       "**Quyền queue trả lời còn thô:** mọi tài khoản chi nhánh đều có thể cấu hình và đọc bất kỳ queue nào tên `reply.*`; mẫu tên riêng cho từng chi nhánh sẽ chặt hơn.",
       "**Chi nhánh trong message do máy gửi tự khai:** broker xác thực tài khoản của từng chi nhánh, nhưng trường `branch_id` trong message do máy gửi tự ghi và chưa được đối chiếu với tài khoản đó; trang quản trị hiện cả hai nên thấy được chỗ lệch, nhưng chưa chặn.",
       "**Worker dùng chung tài khoản quản trị RabbitMQ** thay vì một tài khoản worker quyền tối thiểu.",
       "**Mật khẩu mặc định** vẫn được dùng trên máy lab và phải đổi trước khi mở hệ thống ra rộng hơn.",
       "**Trang quản trị dùng HTTP thường;** chỉ nghe ở địa chỉ loopback của server và được xem qua đường hầm SSH.",
       "**Độ chính xác** phụ thuộc chất lượng camera và ánh sáng và mới đo trên 11 tình nguyện viên (63 ảnh test); ngưỡng cần hiệu chỉnh theo từng triển khai. Model pretrained chỉ dùng phi thương mại.",
       "**Client là ứng dụng trên laptop** (giao diện Tkinter và giao diện web chạy ngay trên máy chi nhánh), chưa phải ứng dụng di động thật; có thể thêm cổng API (ví dụ FastAPI) để điện thoại chụp qua web."),
    P("*Hướng phát triển.* (1) Cụm RabbitMQ ba node với quorum queue nhân bản và replica cho PostgreSQL, MySQL; " +
      "(2) object storage và worker chạy trên nhiều máy; (3) tự động phát hiện và gộp khách trùng (phương án B) song song với khoá, " +
      "dựa trên `tools.reconcile` và công cụ gộp thủ công `tools.merge_customers`; (4) liveness detection cùng chính sách lưu giữ và xoá dữ liệu; (5) tự co giãn " +
      "worker theo độ sâu queue (ví dụ KEDA), kèm metrics và cảnh báo (Prometheus, Grafana); (6) model có giấy phép " +
      "thương mại khi triển khai thật."),

    // ============================================================ IX
    H1("Kết luận"),
    P("Hệ thống đáp ứng yêu cầu của đề bài: nhận diện bằng InsightFace trên ONNX Runtime, giao tiếp qua RabbitMQ, vector " +
      "trong PostgreSQL với pgvector, hồ sơ, ảnh và đơn hàng trong MySQL. Quan trọng hơn, hệ thống hiện thực và kiểm " +
      "chứng các cơ chế phân tán cốt lõi: mở rộng bằng competing consumers, chịu lỗi bằng ack thủ công và giao lại, " +
      "idempotency, saga cho nhất quán hai CSDL, khoá chống race, dead-letter queue, tài khoản chi nhánh quyền tối thiểu có đăng nhập theo chi nhánh, và trang giám sát chỉ đọc " +
      "(TLS có sẵn dưới dạng lớp tuỳ chọn nhưng chưa được thử). Thực nghiệm còn cho một bài học thực tế: mở rộng trên một máy đòi hỏi giới hạn số luồng của mỗi " +
      "worker; khi có giới hạn, sáu worker đạt 2,6× thông lượng của một worker, còn không giới hạn thì thêm worker làm " +
      "hệ thống chậm đi. Giết worker giữa lúc đang xử lý không làm mất và không làm trùng gì. Ngược lại, độ chính xác nhận diện chỉ được kiểm tra trên một bộ dữ liệu nhỏ và mang tính chỉ báo."),
  ],
};
