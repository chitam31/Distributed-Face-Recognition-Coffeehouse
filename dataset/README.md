# Dữ liệu ảnh thực nghiệm

Thu ảnh của **10–20 bạn trong lớp, có sự đồng ý bằng văn bản** (khuôn mặt là dữ liệu sinh trắc học nhạy cảm).
Mỗi người 5–10 ảnh ở ánh sáng và góc khác nhau (thẳng, nghiêng nhẹ, ngược sáng, đeo kính/không...).

```
dataset/
├── nguyen_an/        1.jpg 2.jpg 3.jpg ...
├── tran_binh/        1.jpg 2.jpg ...
└── ...
```

* Tên thư mục = tên khách (không dấu cho gọn).
* 1–2 ảnh đầu mỗi người dùng để *enroll*, phần còn lại để *test* (xem `python -m tools.eval_threshold --help`).
* Với thực nghiệm "người lạ", để riêng vài người **không** enroll (tham số `--unknown-people`).
* Không đưa ảnh thật vào kho mã nguồn công khai (`.gitignore` đã chặn).
