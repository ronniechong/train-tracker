from traintracker.state.eventhub import InProcessEventHub


def test_publish_with_no_subscribers_does_not_raise():
    hub = InProcessEventHub()
    hub.publish("event-1")  # should be a no-op, not an error


def test_subscriber_receives_published_event():
    hub = InProcessEventHub()
    queue = hub.subscribe()
    hub.publish("event-1")
    assert queue.get_nowait() == "event-1"


def test_multiple_subscribers_all_receive_the_same_event():
    hub = InProcessEventHub()
    q1 = hub.subscribe()
    q2 = hub.subscribe()
    hub.publish("event-1")
    assert q1.get_nowait() == "event-1"
    assert q2.get_nowait() == "event-1"


def test_unsubscribe_stops_further_delivery():
    hub = InProcessEventHub()
    queue = hub.subscribe()
    hub.unsubscribe(queue)
    hub.publish("event-1")
    assert queue.empty()
