from asyncio import BaseEventLoop
from concurrent.futures import ProcessPoolExecutor

from servicelib.pools import async_on_threadpool, non_blocking_process_pool_executor


def return_int_one() -> int:
    return 1


async def test_default_thread_pool_executor(event_loop: BaseEventLoop) -> None:
    assert await event_loop.run_in_executor(None, return_int_one) == 1


async def test_blocking_process_pool_executor(event_loop: BaseEventLoop) -> None:
    assert await event_loop.run_in_executor(ProcessPoolExecutor(), return_int_one) == 1


async def test_non_blocking_process_pool_executor(event_loop: BaseEventLoop) -> None:
    with non_blocking_process_pool_executor() as executor:
        assert await event_loop.run_in_executor(executor, return_int_one) == 1


async def test_same_pool_instances() -> None:
    with non_blocking_process_pool_executor() as first, non_blocking_process_pool_executor() as second:
        assert first == second


async def test_different_pool_instances() -> None:
    with non_blocking_process_pool_executor(
        max_workers=1
    ) as first, non_blocking_process_pool_executor() as second:
        assert first != second


async def test_run_on_thread_pool() -> None:
    assert await async_on_threadpool(return_int_one) == 1
