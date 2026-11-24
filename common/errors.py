"""Phân loại lỗi để worker quyết định ack / retry / dead-letter.

* MessageError  (common.messages)  : message sai định dạng -> lỗi vĩnh viễn -> DLQ.
* BusinessError                    : yêu cầu hợp lệ nhưng không thực hiện được
                                     (vd. khách không tồn tại) -> trả ERROR rồi ack.
* TransientError                   : lỗi tạm thời (mất kết nối DB, timeout) -> thử lại.
"""


class TransientError(Exception):
    """Lỗi tạm thời: thử lại có thể thành công (RabbitMQ sẽ giao lại message)."""


class BusinessError(Exception):
    """Lỗi nghiệp vụ: thử lại cũng không đổi kết quả."""
