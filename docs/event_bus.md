# Event Bus

The event bus is a lightweight, decoupled system enabling in-process components to publish events and register subscribers.

## Design
- **Module**: `control/event_bus.py`
- **Class**: `EventBus` (singleton instance `event_bus`)
- **Subscriber Registry**: Maps event types to lists of callback functions.

## Usage

### Event Object
```python
from control.event_bus import Event
event = Event(event_type="TASK_CLAIMED", data={"task_id": "T1", "project": "oi_labs"})
```

### Subscribing
```python
from control.event_bus import event_bus

def on_task_claimed(event):
    print(f"Task claimed: {event.data['task_id']}")

event_bus.subscribe("TASK_CLAIMED", on_task_claimed)
```

### Publishing
```python
event_bus.publish(Event("TASK_CLAIMED", {"task_id": "T1"}))
```

## Resiliency
Subscribers are executed inside try-except blocks. If a subscriber raises an exception, the event bus logs the error and continues executing other subscribers. It never interrupts the publisher or crashes the main thread.
