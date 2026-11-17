import argparse
import logging
from datetime import UTC, datetime, time, timedelta
from random import Random
from uuid import uuid4

from cinema.events import DeviceType, EventType, KafkaPublisher, MovieEvent


MOVIES = ("arrival", "interstellar", "dune", "inception", "the-matrix")


def generate_events(users: int, days: int):
    rng = Random()
    first_day = datetime.now(UTC).date() - timedelta(days=days)
    for _ in range(users):
        user_id = f"demo-{uuid4().hex}"
        joined = rng.randrange(days)
        for offset in range(joined, days):
            age = offset - joined
            if age and rng.random() > 0.75 / (1 + age * 0.18):
                continue
            day = first_day + timedelta(days=offset)
            start = datetime.combine(day, time(rng.randint(10, 20), rng.randrange(60)), UTC)
            movie = rng.choice(MOVIES)
            device = rng.choice(list(DeviceType))
            session = uuid4().hex
            paused_at = rng.randint(120, 1500)
            duration = rng.randint(3600, 7500)
            actions = [
                (EventType.SEARCHED, -rng.randint(15, 90), None),
                (EventType.VIEW_STARTED, 0, 0),
                (EventType.VIEW_PAUSED, paused_at, paused_at),
            ]
            if rng.random() < 0.78:
                pause = rng.randint(20, 180)
                actions.extend([
                    (EventType.VIEW_RESUMED, paused_at + pause, paused_at),
                    (EventType.VIEW_FINISHED, duration + pause, duration),
                ])
                if rng.random() < 0.25:
                    actions.append((EventType.LIKED, duration + pause + 10, None))
            for kind, elapsed, progress in actions:
                yield MovieEvent(
                    event_id=uuid4(), user_id=user_id, movie_id=movie,
                    event_type=kind, device_type=device, session_id=session,
                    timestamp=start + timedelta(seconds=elapsed), progress_seconds=progress,
                )


def main():
    parser = argparse.ArgumentParser(description="Publish synthetic viewing sessions")
    parser.add_argument("--users", type=int, default=30)
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    if not 1 <= args.users <= 500 or not 1 <= args.days <= 60:
        parser.error("use 1–500 users and 1–60 days")
    logging.basicConfig(level=logging.INFO)
    publisher = KafkaPublisher()
    count = 0
    for event in generate_events(args.users, args.days):
        publisher.publish(event)
        count += 1
    print(f"Published {count} events for {args.users} users over {args.days} days")


if __name__ == "__main__":
    main()
