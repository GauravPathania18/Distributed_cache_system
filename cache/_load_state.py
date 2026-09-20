import threading

from typing import Optional


class _LoadState:

    def __init__(self):
        self.done = threading.Event()
        self.error: Optional[BaseException] = None
