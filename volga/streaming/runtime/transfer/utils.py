from typing import Optional


def str_to_bytes(s: str, pad_to_size: Optional[int] = None) -> bytes:
    b = s.encode('utf-8')
    if pad_to_size is None:
        return b
    diff = pad_to_size - len(b)
    if diff < 0:
        raise ValueError('Unable to pad string')
    b += diff * b' '
    return b


def bytes_to_str(b: bytes, strip_padding: bool = False) -> str:
    s = b.decode('utf-8')
    return s.strip() if strip_padding else s


def int_to_bytes(i: int, buff_size: int) -> bytes:
    return i.to_bytes(buff_size, 'big')


def bytes_to_int(b: bytes) -> int:
    return int.from_bytes(b, 'big')
