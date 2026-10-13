class LifecycleEvent:
    """
    One node state transition.

    Delivered to listeners AFTER the lifecycle lock has been
    released, so a listener may safely do HTTP or database work
    (start a warm-up, run a failover repair) without blocking
    routing decisions.

    Fires from real state changes only: registration into an
    initial state and self-loops do not produce events.
    """

    __slots__ = ("node", "old_state", "new_state")

    def __init__(self, node: str, old_state, new_state):

        self.node = node
        self.old_state = old_state
        self.new_state = new_state

    @property
    def old(self) -> str:
        return self.old_state.value if self.old_state is not None else "none"

    @property
    def new(self) -> str:
        return self.new_state.value if self.new_state is not None else "none"

    def __repr__(self):
        return f"LifecycleEvent({self.node}: {self.old} -> {self.new})"


def fire_listeners(manager, event: LifecycleEvent) -> None:
    """
    Deliver `event` to every registered listener.

    Called with the lifecycle lock NOT held. Listener errors are
    isolated: one bad listener must never break the state machine.
    """

    with manager.lock:
        listeners = tuple(manager.listeners)

    for listener in listeners:

        try:
            listener(event)

        except Exception as error:
            print(f"[LISTENER ERROR] {event!r}: {error!r}")
