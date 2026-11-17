from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from cinema.events import BOOTSTRAP_SERVERS, SUBJECT, TOPIC, schema_request


def main():
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    topic = NewTopic(
        TOPIC, num_partitions=3, replication_factor=3,
        config={"min.insync.replicas": "2"},
    )
    try:
        admin.create_topics([topic], request_timeout=30)[TOPIC].result()
    except KafkaException as exc:
        if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise
    schema_request("/versions")
    schema = schema_request()
    print(f"Schema {SUBJECT} v{schema['version']}, id={schema['id']}")


if __name__ == "__main__":
    main()
