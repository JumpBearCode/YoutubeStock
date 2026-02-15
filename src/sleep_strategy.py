import random
import time


def random_sleep(min_seconds: float, max_seconds: float) -> None:
    mode = (min_seconds + max_seconds) / 2
    delay = random.triangular(min_seconds, max_seconds, mode)
    print(f"  Sleeping for {delay:.1f}s...")
    time.sleep(delay)


def inter_channel_sleep(min_seconds: float, max_seconds: float) -> None:
    multiplier = 1.5
    mode = (min_seconds + max_seconds) / 2 * multiplier
    delay = random.triangular(min_seconds * multiplier, max_seconds * multiplier, mode)
    print(f"  Inter-channel sleep for {delay:.1f}s...")
    time.sleep(delay)
