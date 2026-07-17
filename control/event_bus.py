import threading
from typing import Callable, Dict, List, Optional, Any

class Event:
    """Represents a lifecycle event published on the EventBus."""
    
    def __init__(self, name: str, data: Optional[Dict[str, Any]] = None):
        self.name = name
        self.data = data or {}

class EventBus:
    """Lightweight, in-process, thread-safe, non-blocking Event Bus."""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Event], None]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_name: str, callback: Callable[[Event], None]):
        """Registers a callback for a specific event name."""
        with self._lock:
            if event_name not in self._subscribers:
                self._subscribers[event_name] = []
            if callback not in self._subscribers[event_name]:
                self._subscribers[event_name].append(callback)

    def unsubscribe(self, event_name: Optional[str] = None, callback: Optional[Callable[[Event], None]] = None):
        """Unsubscribes registered callbacks.
        
        - If event_name and callback are both None, clears all subscriptions.
        - If callback is None, clears all subscribers for the given event_name.
        - Otherwise, removes the specific callback from the event_name subscribers.
        """
        with self._lock:
            if event_name is None:
                # Clear all subscriptions
                self._subscribers.clear()
            elif callback is None:
                # Clear all for the event
                self._subscribers.pop(event_name, None)
            else:
                # Remove specific callback
                if event_name in self._subscribers:
                    if callback in self._subscribers[event_name]:
                        self._subscribers[event_name].remove(callback)
                    # Clean up empty event key
                    if not self._subscribers[event_name]:
                        del self._subscribers[event_name]

    def publish(self, event: Event):
        """Publishes an event to all registered subscribers.
        
        Asynchronously invokes callbacks in separate threads to remain non-blocking.
        Failing callbacks are isolated and won't affect others.
        """
        with self._lock:
            callbacks = list(self._subscribers.get(event.name, []))
            
        for cb in callbacks:
            # Spawn a thread to execute the callback asynchronously (non-blocking)
            t = threading.Thread(
                target=self._safe_execute,
                args=(cb, event),
                daemon=True
            )
            t.start()

    def _safe_execute(self, callback: Callable[[Event], None], event: Event):
        """Executes a subscriber callback in an exception-safe manner."""
        try:
            callback(event)
        except Exception:
            # Isolated exception block - do not raise to prevent crashing caller/bus threads
            pass

# Global singleton event bus instance
event_bus = EventBus()
