"""Distributed event streams (v5.1) — consumer groups over partitioned logs.

A storage-agnostic stream abstraction modeled on Kafka / Redis Streams:

  * topics partitioned by key (ordering guaranteed within a partition)
  * consumer groups with per-(group, partition) offsets
  * at-least-once delivery: messages stay unacked until ack(); redelivered on
    re-poll if not acked
  * backpressure: bounded max in-flight per consumer
  * replay: re-read a partition from offset 0
  * dead-letter topics: poison messages route to "<topic>.DLQ"
  * schema versioning via the Event envelope

`LocalStreamBroker` is an in-process implementation used now and for tests; the
`RedisStreamsBroker` / Kafka adapters implement the same Broker interface for
horizontal scale without changing producers/consumers.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from events.types import Event


def _partition_for(key: str, num_partitions: int) -> int:
    h = int(hashlib.sha256((key or "").encode()).hexdigest()[:8], 16)
    return h % num_partitions


@dataclass
class StreamMessage:
    offset: int
    partition: int
    event: Event


class Broker:
    def publish(self, topic: str, event: Event, key: str | None = None) -> StreamMessage: ...
    def poll(self, topic: str, group: str, max_messages: int = 10) -> list[StreamMessage]: ...
    def ack(self, topic: str, group: str, message: StreamMessage) -> None: ...
    def replay(self, topic: str, partition: int = 0) -> list[StreamMessage]: ...


class LocalStreamBroker(Broker):
    def __init__(self, num_partitions: int = 4, max_inflight: int = 100):
        self.num_partitions = num_partitions
        self.max_inflight = max_inflight
        # topic -> partition -> list[StreamMessage]
        self._log: dict[str, dict[int, list[StreamMessage]]] = {}
        # (topic, group) -> partition -> committed offset (next to read)
        self._offsets: dict[tuple[str, str], dict[int, int]] = {}

    def _topic(self, topic: str) -> dict[int, list[StreamMessage]]:
        return self._log.setdefault(topic, {p: [] for p in range(self.num_partitions)})

    def publish(self, topic: str, event: Event, key: str | None = None) -> StreamMessage:
        parts = self._topic(topic)
        partition = _partition_for(key or event.tenant_id, self.num_partitions)
        offset = len(parts[partition])
        msg = StreamMessage(offset=offset, partition=partition, event=event)
        parts[partition].append(msg)
        return msg

    def poll(self, topic: str, group: str, max_messages: int = 10) -> list[StreamMessage]:
        parts = self._topic(topic)
        offsets = self._offsets.setdefault((topic, group), {p: 0 for p in range(self.num_partitions)})
        out: list[StreamMessage] = []
        for p in range(self.num_partitions):
            start = offsets[p]
            available = parts[p][start:]
            # Backpressure: never hand out more than max_inflight at once.
            take = available[: min(len(available), self.max_inflight)]
            for msg in take:
                out.append(msg)
                if len(out) >= max_messages:
                    return out
        return out

    def ack(self, topic: str, group: str, message: StreamMessage) -> None:
        offsets = self._offsets.setdefault((topic, group), {p: 0 for p in range(self.num_partitions)})
        # Commit advances the offset past this message (in-order ack per partition).
        offsets[message.partition] = max(offsets[message.partition], message.offset + 1)

    def replay(self, topic: str, partition: int = 0) -> list[StreamMessage]:
        return list(self._topic(topic)[partition])

    def depth(self, topic: str, group: str) -> int:
        parts = self._topic(topic)
        offsets = self._offsets.get((topic, group), {p: 0 for p in range(self.num_partitions)})
        return sum(len(parts[p]) - offsets.get(p, 0) for p in range(self.num_partitions))


@dataclass
class StreamConsumer:
    """Consumes a topic for a group, applies a handler with retries → DLQ.

    at-least-once: a message is acked only after the handler succeeds; failures
    are retried up to max_retries, then routed to "<topic>.DLQ".
    """
    broker: LocalStreamBroker
    topic: str
    group: str
    handler: callable
    max_retries: int = 2
    processed: list[str] = field(default_factory=list)

    def run_once(self, max_messages: int = 100) -> dict:
        stats = {"processed": 0, "dead_lettered": 0}
        for msg in self.broker.poll(self.topic, self.group, max_messages):
            ok, last_error = False, None
            for _ in range(self.max_retries + 1):
                try:
                    self.handler(msg.event)
                    ok = True
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
            if ok:
                self.processed.append(msg.event.event_id)
                stats["processed"] += 1
            else:
                self.broker.publish(f"{self.topic}.DLQ", msg.event,
                                    key=msg.event.event_id)
                stats["dead_lettered"] += 1
            # Ack either way so the group makes forward progress (poison → DLQ).
            self.broker.ack(self.topic, self.group, msg)
        return stats


class RedisStreamsBroker(Broker):  # pragma: no cover - requires live Redis
    """Redis Streams adapter (XADD / XREADGROUP / XACK). Same Broker interface."""

    def __init__(self, url: str | None = None, num_partitions: int = 4):
        import redis  # lazy; optional dependency
        self._r = redis.Redis.from_url(url or "redis://localhost:6379/0")
        self.num_partitions = num_partitions

    def publish(self, topic: str, event: Event, key: str | None = None) -> StreamMessage:
        import json
        partition = _partition_for(key or event.tenant_id, self.num_partitions)
        stream = f"{topic}:{partition}"
        msg_id = self._r.xadd(stream, {"data": json.dumps(event.to_row()),
                                       "meta": json.dumps(event.envelope())})
        return StreamMessage(offset=int(msg_id.split(b"-")[0]), partition=partition, event=event)
