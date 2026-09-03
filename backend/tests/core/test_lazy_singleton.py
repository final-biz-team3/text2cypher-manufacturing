"""LazySingleton이 지연 생성·재사용·close 후 재생성을 정확히 하는지 검증한다."""

from core.lazy_singleton import LazySingleton


def test_get_creates_instance_only_once() -> None:
    create_calls = []

    def factory() -> object:
        instance = object()
        create_calls.append(instance)
        return instance

    async def closer(_: object) -> None:
        pass

    singleton = LazySingleton(factory, closer)

    first = singleton.get()
    second = singleton.get()

    assert first is second
    assert len(create_calls) == 1


def test_get_does_not_create_until_first_call() -> None:
    create_calls = []

    def factory() -> object:
        create_calls.append(object())
        return create_calls[-1]

    async def closer(_: object) -> None:
        pass

    LazySingleton(factory, closer)

    assert create_calls == []


async def test_close_invokes_closer_and_resets_instance() -> None:
    closed: list[object] = []

    def factory() -> object:
        return object()

    async def closer(instance: object) -> None:
        closed.append(instance)

    singleton = LazySingleton(factory, closer)
    instance = singleton.get()

    await singleton.close()

    assert closed == [instance]


async def test_close_before_any_get_does_not_call_closer() -> None:
    closed: list[object] = []

    def factory() -> object:
        return object()

    async def closer(instance: object) -> None:
        closed.append(instance)

    singleton = LazySingleton(factory, closer)

    await singleton.close()

    assert closed == []


async def test_get_after_close_creates_a_new_instance() -> None:
    create_calls = []

    def factory() -> object:
        instance = object()
        create_calls.append(instance)
        return instance

    async def closer(_: object) -> None:
        pass

    singleton = LazySingleton(factory, closer)
    first = singleton.get()
    await singleton.close()
    second = singleton.get()

    assert first is not second
    assert len(create_calls) == 2
