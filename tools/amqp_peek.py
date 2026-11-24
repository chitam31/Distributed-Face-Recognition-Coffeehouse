"""Xem nội dung hàng đợi mà không làm mất message (tách riêng, không phụ thuộc pika để test được)."""


def peek_queue(ch, queue_name: str, limit: int = 10) -> list:
    """Xem tối đa `limit` message đầu hàng đợi rồi trả lại TẤT CẢ, không làm mất.

    Lấy hết trước (giữ ở trạng thái chưa ack) rồi mới nack một lần: nếu nack từng cái ngay sau khi lấy
    thì message vừa trả lại nằm đầu hàng đợi và vòng sau lại lấy đúng nó (in cùng một message nhiều lần).
    """
    seen, last_tag = [], None
    while len(seen) < limit:
        method, props, body = ch.basic_get(queue_name, auto_ack=False)
        if method is None:
            break
        seen.append((props, body))
        last_tag = method.delivery_tag
    if last_tag is not None:
        ch.basic_nack(delivery_tag=last_tag, multiple=True, requeue=True)
    return seen
