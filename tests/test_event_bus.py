import unittest
import time
import threading
from control.event_bus import EventBus, Event

class TestEventBus(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()

    def tearDown(self):
        self.bus.unsubscribe()  # Unsubscribe all

    def test_publish(self):
        # Setup subscriber callback
        received_events = []
        def on_event(event):
            received_events.append(event)
            
        self.bus.subscribe("TEST_EVENT", on_event)
        
        # Publish event
        evt = Event("TEST_EVENT", {"key": "value"})
        self.bus.publish(evt)
        
        # Wait briefly for asynchronous execution
        time.sleep(0.05)
        
        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0].name, "TEST_EVENT")
        self.assertEqual(received_events[0].data["key"], "value")

    def test_multiple_subscribers(self):
        received_1 = []
        received_2 = []
        
        self.bus.subscribe("MULTIPLE", lambda e: received_1.append(e))
        self.bus.subscribe("MULTIPLE", lambda e: received_2.append(e))
        
        self.bus.publish(Event("MULTIPLE", {"data": "yes"}))
        time.sleep(0.05)
        
        self.assertEqual(len(received_1), 1)
        self.assertEqual(len(received_2), 1)

    def test_unsubscribe(self):
        received = []
        callback = lambda e: received.append(e)
        
        # 1. Subscribe
        self.bus.subscribe("UNSUB", callback)
        self.bus.publish(Event("UNSUB"))
        time.sleep(0.05)
        self.assertEqual(len(received), 1)
        
        # 2. Unsubscribe specific callback
        self.bus.unsubscribe("UNSUB", callback)
        self.bus.publish(Event("UNSUB"))
        time.sleep(0.05)
        self.assertEqual(len(received), 1)  # Count remains 1 since unsubscribed

        # 3. Unsubscribe all for event
        received2 = []
        self.bus.subscribe("UNSUB_ALL", lambda e: received2.append(e))
        self.bus.unsubscribe("UNSUB_ALL")
        self.bus.publish(Event("UNSUB_ALL"))
        time.sleep(0.05)
        self.assertEqual(len(received2), 0)

    def test_callback_isolation(self):
        # One failing subscriber must never affect others
        received = []
        
        def failing_callback(event):
            raise RuntimeError("Expected subscriber failure")
            
        def successful_callback(event):
            received.append(event)
            
        self.bus.subscribe("FAIL_TEST", failing_callback)
        self.bus.subscribe("FAIL_TEST", successful_callback)
        
        # Publish should complete without raising any exceptions
        try:
            self.bus.publish(Event("FAIL_TEST"))
        except Exception as e:
            self.fail(f"Publish raised an exception: {e}")
            
        time.sleep(0.05)
        
        # Verify the successful callback was still executed
        self.assertEqual(len(received), 1)

    def test_non_blocking_publish(self):
        # publish() must return immediately even if callback takes time
        block_event = threading.Event()
        start_time = time.time()
        
        def slow_callback(event):
            block_event.wait()
            
        self.bus.subscribe("SLOW", slow_callback)
        
        # Publish
        self.bus.publish(Event("SLOW"))
        end_time = time.time()
        
        # Verify publish returns immediately (within a few milliseconds)
        self.assertLess(end_time - start_time, 0.05)
        
        # Release block
        block_event.set()
        time.sleep(0.05)

    def test_thread_safety(self):
        # Concurrently subscribe, unsubscribe, and publish under load
        num_threads = 20
        threads = []
        
        def subscriber_thread(index):
            for _ in range(50):
                cb = lambda e: None
                self.bus.subscribe(f"EVENT_{index}", cb)
                self.bus.publish(Event(f"EVENT_{index}"))
                self.bus.unsubscribe(f"EVENT_{index}", cb)
                
        for i in range(num_threads):
            t = threading.Thread(target=subscriber_thread, args=(i,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        # If no deadlock or concurrency crash occurred, test passes
        self.assertTrue(True)

if __name__ == "__main__":
    unittest.main()
