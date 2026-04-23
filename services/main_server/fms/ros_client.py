import os
import threading
import roslibpy

STUB = os.getenv("ROS_STUB", "0") == "1"

class ROSClient:
    def __init__(self, host="localhost", port=9090):
        self.host, self.port = host, port
        self.client = None
        self._publishers = {}
        self._subscribers = {}
        self._lock = threading.Lock()

    def connect(self):
        if STUB:
            print("[ros_client] STUB mode — rosbridge 연결 생략")
            return
        self.client = roslibpy.Ros(host=self.host, port=self.port)
        self.client.run()
        print(f"[ros_client] connected to ws://{self.host}:{self.port}")

    def close(self):
        if self.client and self.client.is_connected:
            self.client.terminate()

    def is_connected(self):
        if STUB:
            return False
        return self.client is not None and self.client.is_connected

    def publish(self, topic, msg_type, message):
        if STUB:
            print(f"[STUB publish] {topic} <- {message}")
            return
        with self._lock:
            pub = self._publishers.get(topic)
            if pub is None:
                pub = roslibpy.Topic(self.client, topic, msg_type)
                pub.advertise()
                self._publishers[topic] = pub
        pub.publish(roslibpy.Message(message))

    def subscribe(self, topic, msg_type, callback):
        if STUB:
            print(f"[STUB subscribe] {topic}")
            return
        with self._lock:
            if topic in self._subscribers:
                return
            sub = roslibpy.Topic(self.client, topic, msg_type)
            sub.subscribe(callback)
            self._subscribers[topic] = sub
        print(f"[ros_client] subscribed to {topic}")

    def unsubscribe(self, topic):
        with self._lock:
            sub = self._subscribers.pop(topic, None)
        if sub:
            sub.unsubscribe()

ros = ROSClient(
    host=os.getenv("ROS_HOST", "localhost"),
    port=int(os.getenv("ROS_PORT", "9090")),
)
